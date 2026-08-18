from __future__ import annotations

import difflib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Optional

import click

from urllib.parse import urlparse

from awerouter import __version__
from awerouter.protocols import PROTOCOL_IDS
from awerouter.types import AutoThresholdConfig, Destination, Provider, RoutingProfile, Settings, ToolRoutingConfig

# ---------------------------------------------------------------------------
# Constants (mirror aweswitch cli.py conventions exactly)
# ---------------------------------------------------------------------------

ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
SECRET_RE = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD|AUTH)", re.IGNORECASE)

# Implicit listen port: the first serve instance takes it, later ones scan up.
DEFAULT_PORT = 20128

TEMPLATE_PROVIDERS = Path(__file__).parent / "default-providers.json"
TEMPLATE_ROUTING = Path(__file__).parent / "default-routing.json"


def die(message: str) -> "SystemExit":
    raise SystemExit(f"awerouter: {message}")


def detect_auth_header(base_url: str) -> str:
    """Auto-detect auth header from base_url.

    anthropic.com endpoints use x-api-key (bare token); everyone else uses
    Authorization (Bearer prefix added at request time). Matched on netloc,
    not substring — "https://evil.com/anthropic.com" must not match.
    """
    netloc = urlparse(base_url).netloc.lower()
    is_anthropic = netloc == "api.anthropic.com" or netloc.endswith(".anthropic.com")
    return "x-api-key" if is_anthropic else "authorization"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def config_dir() -> Path:
    return Path(os.environ.get("AWEROUTER_CONFIG_DIR", "~/.config/awerouter")).expanduser()


def providers_path() -> Path:
    return config_dir() / "providers.json"


def routing_path() -> Path:
    return config_dir() / "routing.json"


def backup_path(p: Path) -> Path:
    """Single-slot .bak sibling of a config file (aweswitch convention)."""
    return p.with_name(p.name + ".bak")


def backup_file(p: Path) -> "Path | None":
    """Snapshot p to its .bak before an awerouter-mediated write."""
    if not p.exists():
        return None
    bak = backup_path(p)
    shutil.copy2(p, bak)
    return bak


# ---------------------------------------------------------------------------
# Value helpers (mirror aweswitch exactly)
# ---------------------------------------------------------------------------

def expand_value(value, env: dict) -> "str | int | float | bool | None":
    if not isinstance(value, str):
        return value

    def replace(match):
        name = match.group(1)
        if name not in env:
            die(
                f"required environment variable not set: {name}\n"
                f"  Add it to your shell config (e.g. ~/.zshrc or ~/.bashrc), then reload your shell."
            )
        return env[name]

    return ENV_REF_RE.sub(replace, value)


def redact(data):
    redacted = json.loads(json.dumps(data))  # deep copy via JSON

    def walk(value, key=""):
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                if SECRET_RE.search(child_key) and isinstance(child_value, str):
                    value[child_key] = "<redacted>"
                else:
                    walk(child_value, child_key)
        elif isinstance(value, list):
            for item in value:
                walk(item, key)

    walk(redacted)
    return redacted


# ---------------------------------------------------------------------------
# Load / validate
# ---------------------------------------------------------------------------

def _load_json(path: Path, label: str) -> dict:
    if not path.exists():
        die(f"{label} not found: {path}\nrun: awerouter init")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        die(f"invalid JSON in {path}: {exc}")
    if not isinstance(data, dict):
        die(f"{label} must be a JSON object: {path}")
    return data


def _parse_destination(raw: str) -> Destination:
    parts = raw.split(",", 1)
    if len(parts) != 2:
        die(f"destination must be 'provider,model': {raw}")
    provider_name, model = parts[0].strip(), parts[1].strip()
    if not provider_name or not model:
        die(f"destination must be 'provider,model': {raw}")
    return Destination(provider_name=provider_name, model=model)


_OLD_AGENT_GROUPS = {"claude": "anthropic", "codex": "openai-chat / openai-responses"}


def _die_bad_protocol_group(key: str) -> "SystemExit":
    if key in _OLD_AGENT_GROUPS:
        return die(
            f"providers.json group '{key}' uses the old agent names — rename: "
            + ", ".join(f"'{k}' → {v}" for k, v in _OLD_AGENT_GROUPS.items())
        )
    return die(
        f"providers.json group '{key}' must be a protocol id: "
        f"{', '.join(PROTOCOL_IDS)}"
    )


def load_providers(path: Optional[Path] = None) -> dict[str, dict[str, Provider]]:
    """Load providers grouped by protocol. Returns {protocol: {provider_name: Provider}}."""
    path = path or providers_path()
    data = _load_json(path, "providers.json")
    result: dict[str, dict[str, Provider]] = {}
    for protocol, group in data.items():
        if protocol not in PROTOCOL_IDS:
            _die_bad_protocol_group(protocol)
        if not isinstance(group, dict):
            die(f"protocol group '{protocol}' must be an object")
        group_providers: dict[str, Provider] = {}
        for name, entry in group.items():
            if not isinstance(entry, dict):
                die(f"provider '{protocol}.{name}' must be an object")
            base_url = entry.get("base_url")
            auth = entry.get("auth")
            if not base_url or not auth:
                die(f"provider '{protocol}.{name}' missing base_url or auth")
            auth_header = entry.get("auth_header") or detect_auth_header(base_url)
            group_providers[name] = Provider(
                name=name, base_url=base_url, auth=auth, auth_header=auth_header,
            )
        result[protocol] = group_providers
    return result


def _parse_auto_threshold(raw) -> AutoThresholdConfig:
    """Parse settings.longContextAuto (all fields optional; defaults in the type)."""
    if raw is None:
        return AutoThresholdConfig()
    if not isinstance(raw, dict):
        die("routing.json settings 'longContextAuto' must be an object")

    def int_field(key: str, lo: int, hi: int | None = None) -> "int | None":
        if key not in raw:
            return None
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, int):
            die(f"routing.json settings longContextAuto '{key}' must be an integer, got: {value!r}")
        bound = f" in {lo}-{hi}" if hi is not None else f" >= {lo}"
        if value < lo or (hi is not None and value > hi):
            die(f"routing.json settings longContextAuto '{key}' must be{bound}, got: {value}")
        return value

    cfg = AutoThresholdConfig()
    if (v := int_field("percentile", 1, 99)) is not None:
        cfg.percentile = v
    if (v := int_field("windowDays", 1)) is not None:
        cfg.window_days = v
    if (v := int_field("minSamples", 1)) is not None:
        cfg.min_samples = v
    if (v := int_field("fallbackThreshold", 0)) is not None:
        cfg.fallback_threshold = v
    return cfg


def _parse_tool_routing(raw) -> ToolRoutingConfig:
    """settings.toolRouting: {"search": "flash", "edit": "pro"} — destination
    keys or null to disable a rule. Absent block = both defaults on."""
    if raw is None:
        return ToolRoutingConfig()
    if not isinstance(raw, dict):
        die("routing.json settings 'toolRouting' must be an object")
    cfg = ToolRoutingConfig()
    for field_name in ("search", "edit"):
        value = raw.get(field_name, getattr(cfg, field_name))
        if value is not None and value not in ("flash", "pro"):
            die(f"routing.json settings toolRouting '{field_name}' must be "
                f"'flash', 'pro', or null, got: {value!r}")
        setattr(cfg, field_name, value)
    return cfg


def load_routing(path: Optional[Path] = None) -> tuple[Settings, dict[str, RoutingProfile]]:
    """Load global settings + all routing profiles keyed by profile id."""
    path = path or routing_path()
    data = _load_json(path, "routing.json")

    # Parse optional global settings (defaults: flash/pro)
    raw_settings = data.pop("settings", {})
    if not isinstance(raw_settings, dict):
        die("routing.json 'settings' must be an object")
    raw_discount = raw_settings.get("searchResultDiscount", 0.3)
    try:
        discount = float(raw_discount)
    except (TypeError, ValueError):
        die(f"routing.json settings 'searchResultDiscount' must be a number in [0, 1], got: {raw_discount!r}")
    if not 0 <= discount <= 1:
        die(f"routing.json settings 'searchResultDiscount' must be in [0, 1], got: {discount}")
    settings = Settings(
        background_model=str(raw_settings.get("backgroundModel", "flash")),
        think_model=str(raw_settings.get("thinkModel", "pro")),
        web_search_model=str(raw_settings.get("webSearchModel", "pro")),
        search_result_discount=discount,
        long_context_auto=_parse_auto_threshold(raw_settings.get("longContextAuto")),
        tool_routing=_parse_tool_routing(raw_settings.get("toolRouting")),
    )

    profiles: dict[str, RoutingProfile] = {}
    for name, body in data.items():
        if not isinstance(body, dict):
            die(f"profile '{name}' must be an object")
        if "agent" in body:
            die(
                f"profile '{name}': 'agent' was renamed to 'protocol' "
                "(claude → anthropic, codex → openai-responses); edit routing.json"
            )
        protocol = body.get("protocol")
        if not protocol:
            die(f"profile '{name}' missing required 'protocol' field")
        if protocol not in PROTOCOL_IDS:
            die(
                f"profile '{name}': unknown protocol '{protocol}'; "
                f"expected one of: {', '.join(PROTOCOL_IDS)}"
            )
        for key in ("longContextThreshold", "destinations"):
            if key not in body:
                die(f"profile '{name}' missing required key: {key}")
        port_raw = body.get("port")
        if port_raw is not None:
            if isinstance(port_raw, bool) or not isinstance(port_raw, int) or not 1 <= port_raw <= 65535:
                die(f"profile '{name}': 'port' must be an integer in 1-65535, got: {port_raw!r}")
        dests_raw = body["destinations"]
        if not isinstance(dests_raw, dict):
            die(f"profile '{name}' destinations must be an object")
        parsed: dict[str, Destination] = {}
        for tier, raw in dests_raw.items():
            if tier not in ("flash", "pro"):
                die(f"profile '{name}' destination key must be flash or pro, got: {tier}")
            parsed[tier] = _parse_destination(str(raw))
        raw_threshold = body["longContextThreshold"]
        if raw_threshold == "auto":
            # Resolved at serve start; the fallback value keeps reads before
            # that (config show, tests) meaningful.
            threshold_auto = True
            threshold = settings.long_context_auto.fallback_threshold
        else:
            threshold_auto = False
            try:
                threshold = int(raw_threshold)
            except (TypeError, ValueError):
                die(f"profile '{name}': 'longContextThreshold' must be a non-negative "
                    f"integer or \"auto\", got: {raw_threshold!r}")
            if threshold < 0:
                die(f"profile '{name}': 'longContextThreshold' must be a non-negative "
                    f"integer or \"auto\", got: {threshold}")
        profiles[name] = RoutingProfile(
            name=name,
            protocol=str(protocol),
            long_context_threshold=threshold,
            destinations=parsed,
            port=port_raw,
            threshold_auto=threshold_auto,
        )
    return settings, profiles


def resolve_provider(name: str, providers: dict[str, Provider]) -> Provider:
    if name not in providers:
        avail = ", ".join(providers) or "(none)"
        die(f"provider '{name}' not found in this profile's agent group; available: {avail}")
    return providers[name]


def validate_profiles(providers_all: dict, profiles: dict) -> None:
    """Cross-check every profile's protocol and destinations against providers.json.

    Called by both serve and config show, so bad references fail at load time
    instead of on the first request.
    """
    for profile in profiles.values():
        group = providers_all.get(profile.protocol)
        if group is None:
            avail = ", ".join(providers_all) or "(none)"
            die(
                f"protocol '{profile.protocol}' (for profile '{profile.name}') not found in "
                f"providers.json; available: {avail}"
            )
        for tier, dest in profile.destinations.items():
            resolve_provider(dest.provider_name, group)


def load_for_profile(name: str) -> tuple[dict[str, Provider], RoutingProfile, Settings]:
    """Resolve one profile: returns (protocol providers, profile, settings)."""
    providers_all = load_providers()
    settings, profiles = load_routing()
    if name not in profiles:
        avail = ", ".join(profiles) or "(none)"
        die(f"profile '{name}' not found in routing.json; available: {avail}")
    profile = profiles[name]
    validate_profiles(providers_all, {name: profile})
    return providers_all[profile.protocol], profile, settings


def load_default_profile() -> tuple[dict[str, Provider], RoutingProfile, Settings]:
    """Auto-select when only one profile exists; prompt otherwise."""
    settings, profiles = load_routing()
    if not profiles:
        die("no profiles in routing.json")
    if len(profiles) == 1:
        return load_for_profile(next(iter(profiles)))
    die(
        "multiple profiles available, specify one:\n"
        f"  awerouter serve <name>\navailable: {', '.join(profiles)}"
    )


# ---------------------------------------------------------------------------
# Init / template
# ---------------------------------------------------------------------------

def init_config() -> None:
    d = config_dir()
    if providers_path().exists() or routing_path().exists():
        die(f"config already exists in {d}")
    d.mkdir(parents=True, exist_ok=True)
    if not TEMPLATE_PROVIDERS.exists() or not TEMPLATE_ROUTING.exists():
        die("default templates not found next to config.py")
    shutil.copy2(TEMPLATE_PROVIDERS, providers_path())
    shutil.copy2(TEMPLATE_ROUTING, routing_path())


def save_provider(protocol: str, name: str, base_url: str, auth: str) -> None:
    """Append one provider entry to providers.json."""
    path = providers_path()
    data = _load_json(path, "providers.json")
    group = data.setdefault(protocol, {})
    if name in group:
        die(f"provider already exists: {protocol}.{name}")
    group[name] = {"base_url": base_url, "auth": auth}
    backup_file(path)
    path.write_text(json.dumps(data, indent=2) + "\n")


def save_profile_entry(
    name: str, protocol: str, long_context_threshold: "int | str", flash: str, pro: str
) -> None:
    """Append one profile entry to routing.json. flash/pro are 'provider,model';
    long_context_threshold may be the string "auto"."""
    path = routing_path()
    data = _load_json(path, "routing.json")
    if name in data:
        die(f"profile already exists: {name}")
    data[name] = {
        "protocol": protocol,
        "longContextThreshold": long_context_threshold,
        "destinations": {"flash": flash, "pro": pro},
    }
    backup_file(path)
    path.write_text(json.dumps(data, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Config display
# ---------------------------------------------------------------------------

def format_providers_display(all_providers: dict[str, dict[str, Provider]]) -> str:
    display = {}
    for protocol, group in all_providers.items():
        protocol_display = {}
        for name, p in group.items():
            entry = {"base_url": p.base_url, "auth_header": p.auth_header}
            if ENV_REF_RE.fullmatch(str(p.auth)):
                entry["auth"] = str(p.auth)
            else:
                entry["auth"] = "<set>"
            protocol_display[name] = entry
        display[protocol] = protocol_display
    return json.dumps(display, indent=2)


def format_routing_display(settings: Settings, profiles: dict[str, RoutingProfile]) -> str:
    display = {
        "settings": {
            "backgroundModel": settings.background_model,
            "thinkModel": settings.think_model,
            "webSearchModel": settings.web_search_model,
            "searchResultDiscount": settings.search_result_discount,
            "longContextAuto": {
                "percentile": settings.long_context_auto.percentile,
                "windowDays": settings.long_context_auto.window_days,
                "minSamples": settings.long_context_auto.min_samples,
                "fallbackThreshold": settings.long_context_auto.fallback_threshold,
            },
        },
    }
    for name, p in profiles.items():
        entry = {
            "protocol": p.protocol,
            "longContextThreshold": "auto" if p.threshold_auto else p.long_context_threshold,
        }
        if p.port is not None:
            entry["port"] = p.port
        entry["destinations"] = {
            k: f"{v.provider_name},{v.model}" for k, v in p.destinations.items()
        }
        display[name] = entry
    return json.dumps(display, indent=2)


# ---------------------------------------------------------------------------
# Click CLI
# ---------------------------------------------------------------------------

def _close_command(group: click.Group, ctx, cmd_name: str) -> "str | None":
    """Closest real command name for a typo'd token, or None."""
    names = [c for c in group.list_commands(ctx) if not c.startswith("__")]
    close = difflib.get_close_matches(cmd_name, names, n=1, cutoff=0.8)
    return close[0] if close else None


class SuggestGroup(click.Group):
    """Group that turns unknown subcommands into friendly errors:
    a close match gets a did-you-mean suggestion, anything else gets a
    pointer to `-h` — never a bare usage error."""

    def resolve_command(self, ctx, args):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            if not args:
                raise
            cmd_name = args[0]
            close = _close_command(self, ctx, cmd_name)
            if close:
                ctx.fail(
                    f"unknown command '{cmd_name}' — did you mean '{close}'? "
                    f"(run {ctx.command_path} -h for usage)"
                )
            ctx.fail(
                f"unknown command '{cmd_name}' — "
                f"run {ctx.command_path} -h to list commands"
            )


class ProfileGroup(SuggestGroup):
    """Group where an unknown subcommand is treated as a profile name:
    `awerouter cc-router-1` == `awerouter serve cc-router-1`.

    Defined commands always win, so profiles named after commands are
    unreachable via the shorthand (use `serve <name>` for those). A token that
    closely resembles a real command (e.g. `server` vs `serve`) is reported as
    a typo instead of being taken as a profile name; a bare unknown token with
    extra positional arguments can't be a profile launch either, so it gets
    the -h pointer.
    """

    def resolve_command(self, ctx, args):
        try:
            # Bypass SuggestGroup: unknown tokens may be profile names.
            return click.Group.resolve_command(self, ctx, args)
        except click.UsageError:
            if not args:
                raise
            cmd_name = args[0]
            close = _close_command(self, ctx, cmd_name)
            if close:
                ctx.fail(
                    f"unknown command '{cmd_name}' — did you mean '{close}'? "
                    f"(to start a profile: awerouter serve <profile> or awerouter <profile>)"
                )
            if self._has_stray_positionals(args[1:]):
                ctx.fail(
                    f"unknown command '{cmd_name}' — run {ctx.command_path} -h to list commands"
                )
            ctx.meta["profile_name"] = cmd_name
            command = self.get_command(ctx, "__serve_profile__")
            return args[0], command, args[1:]

    @staticmethod
    def _has_stray_positionals(rest: list) -> bool:
        """True when any arg is a positional, not an option or an option value.

        The bare-profile launch only accepts --port/--host, so leftover
        positionals mean this can't be a valid profile invocation.
        """
        expect_value = False
        for a in rest:
            if expect_value:
                expect_value = False
                continue
            if a in ("--port", "--host"):
                expect_value = True
                continue
            if a.startswith("-"):
                continue
            return True
        return False


@click.group(
    cls=ProfileGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(__version__, "-v", "--version", message="awerouter %(version)s")
def cli():
    """Smart LLM router: fast cheap tasks to flash, hard decisions to pro."""


@cli.group(cls=SuggestGroup, context_settings={"help_option_names": ["-h", "--help"]})
def config():
    """Manage awerouter config."""


@config.command("path")
def config_path_cmd():
    """Print both config file paths."""
    click.echo(providers_path())
    click.echo(routing_path())


@config.command("show")
@click.argument("profile", required=False)
def config_show_cmd(profile):
    """Show config, secrets redacted; with PROFILE, only its view."""
    providers_all = load_providers()
    settings, profiles = load_routing()
    validate_profiles(providers_all, profiles)
    if not profile:
        click.echo("providers.json:")
        click.echo(format_providers_display(providers_all))
        click.echo()
        click.echo("routing.json:")
        click.echo(format_routing_display(settings, profiles))
        return
    if profile not in profiles:
        avail = ", ".join(profiles) or "(none)"
        die(f"profile '{profile}' not found in routing.json; available: {avail}")
    p = profiles[profile]
    used = {d.provider_name: providers_all[p.protocol][d.provider_name]
            for d in p.destinations.values()}
    click.echo("providers:")
    click.echo(format_providers_display({p.protocol: used}))
    click.echo()
    click.echo("profile:")
    click.echo(format_routing_display(settings, {profile: p}))


@config.command("edit")
@click.argument("file", required=False,
                type=click.Choice(["providers", "routing"], case_sensitive=False))
def config_edit_cmd(file):
    """Open providers.json or routing.json in $EDITOR (default config created if missing)."""
    config_dir().mkdir(parents=True, exist_ok=True)
    if not providers_path().exists() or not routing_path().exists():
        init_config()
    if file is None:
        file = click.prompt("File to edit", type=click.Choice(["providers", "routing"]))
    target = providers_path() if file == "providers" else routing_path()
    backup = backup_file(target)
    if backup:
        click.echo(f"backup: {backup}")
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or shutil.which("nano")
    if not editor:
        die("no EDITOR set; edit config manually")
    import subprocess
    import sys
    if os.name == "nt":
        result = subprocess.run([editor, str(target)])
        sys.exit(result.returncode)
    else:
        os.execvp(editor, [editor, str(target)])


@cli.command("init")
def init_cmd():
    """Create default config from templates."""
    init_config()
    click.echo(config_dir())


def main(argv=None):
    try:
        return cli.main(args=argv, prog_name="awerouter")
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
