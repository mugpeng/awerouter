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
from awerouter.types import Destination, Provider, RoutingProfile, Settings

# ---------------------------------------------------------------------------
# Constants (mirror aweswitch cli.py conventions exactly)
# ---------------------------------------------------------------------------

ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
SECRET_RE = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD|AUTH)", re.IGNORECASE)

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
        die(f"{label} not found: {path}\nrun: awerouter config init")
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


def load_routing(path: Optional[Path] = None) -> tuple[Settings, dict[str, RoutingProfile]]:
    """Load global settings + all routing profiles keyed by profile id."""
    path = path or routing_path()
    data = _load_json(path, "routing.json")

    # Parse optional global settings (defaults: flash/pro)
    raw_settings = data.pop("settings", {})
    if not isinstance(raw_settings, dict):
        die("routing.json 'settings' must be an object")
    settings = Settings(
        background_model=str(raw_settings.get("backgroundModel", "flash")),
        think_model=str(raw_settings.get("thinkModel", "pro")),
        web_search_model=str(raw_settings.get("webSearchModel", "pro")),
    )

    profiles: dict[str, RoutingProfile] = {}
    for name, body in data.items():
        if not isinstance(body, dict):
            die(f"profile '{name}' must be an object")
        if "agent" in body:
            die(
                f"profile '{name}': 'agent' was renamed to 'protocol' "
                "(claude → anthropic, codex → openai-chat / openai-responses); edit routing.json"
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
        dests_raw = body["destinations"]
        if not isinstance(dests_raw, dict):
            die(f"profile '{name}' destinations must be an object")
        parsed: dict[str, Destination] = {}
        for tier, raw in dests_raw.items():
            if tier not in ("flash", "pro"):
                die(f"profile '{name}' destination key must be flash or pro, got: {tier}")
            parsed[tier] = _parse_destination(str(raw))
        profiles[name] = RoutingProfile(
            name=name,
            protocol=str(protocol),
            long_context_threshold=int(body["longContextThreshold"]),
            destinations=parsed,
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
    path.write_text(json.dumps(data, indent=2) + "\n")


def save_profile_entry(
    name: str, protocol: str, long_context_threshold: int, flash: str, pro: str
) -> None:
    """Append one profile entry to routing.json. flash/pro are 'provider,model'."""
    path = routing_path()
    data = _load_json(path, "routing.json")
    if name in data:
        die(f"profile already exists: {name}")
    data[name] = {
        "protocol": protocol,
        "longContextThreshold": long_context_threshold,
        "destinations": {"flash": flash, "pro": pro},
    }
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
        },
    }
    for name, p in profiles.items():
        display[name] = {
            "protocol": p.protocol,
            "longContextThreshold": p.long_context_threshold,
            "destinations": {
                k: f"{v.provider_name},{v.model}" for k, v in p.destinations.items()
            },
        }
    return json.dumps(display, indent=2)


# ---------------------------------------------------------------------------
# Click CLI
# ---------------------------------------------------------------------------

class ProfileGroup(click.Group):
    """Group where an unknown subcommand is treated as a profile name:
    `awerouter cc-router-1` == `awerouter serve cc-router-1`.

    Defined commands always win, so profiles named after commands are
    unreachable via the shorthand (use `serve <name>` for those).
    """

    def resolve_command(self, ctx, args):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            if not args:
                raise
            ctx.meta["profile_name"] = args[0]
            command = self.get_command(ctx, "__serve_profile__")
            return args[0], command, args[1:]


@click.group(
    cls=ProfileGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(__version__, "-v", "--version", message="awerouter %(version)s")
def cli():
    """Smart LLM router: fast cheap tasks to flash, hard decisions to pro."""


@cli.group(context_settings={"help_option_names": ["-h", "--help"]})
def config():
    """Manage awerouter config."""


@config.command("path")
def config_path_cmd():
    """Print config directory path."""
    click.echo(config_dir())


@config.command("show")
def config_show_cmd():
    """Show config (secrets redacted)."""
    providers_all = load_providers()
    settings, profiles = load_routing()
    validate_profiles(providers_all, profiles)
    click.echo("providers.json:" )
    click.echo(format_providers_display(providers_all))
    click.echo()
    click.echo("routing.json:")
    click.echo(format_routing_display(settings, profiles))


@config.command("edit")
def config_edit_cmd():
    """Open config dir in $EDITOR (creates default config if missing)."""
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    if not providers_path().exists() or not routing_path().exists():
        init_config()
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or shutil.which("nano")
    if not editor:
        die("no EDITOR set; edit config manually")
    import subprocess
    import sys
    if os.name == "nt":
        argv = [editor, str(d)]
        result = subprocess.run(argv)
        sys.exit(result.returncode)
    else:
        os.execvp(editor, [editor, str(d)])


@config.command("init")
def config_init_cmd():
    """Create default config from templates."""
    init_config()
    click.echo(config_dir())


@cli.command("init")
def init_cmd():
    """Create default config from templates (same as config init)."""
    init_config()
    click.echo(config_dir())


def main(argv=None):
    try:
        return cli.main(args=argv, prog_name="awerouter")
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
