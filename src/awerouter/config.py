import json
import os
import re
import shutil
from pathlib import Path
from typing import Optional

import click

from awerouter import __version__
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
    Authorization (Bearer prefix added at request time).
    """
    return "x-api-key" if "anthropic.com" in base_url else "authorization"


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


def load_providers(path: Optional[Path] = None) -> dict[str, dict[str, Provider]]:
    """Load providers grouped by agent. Returns {agent: {provider_name: Provider}}."""
    path = path or providers_path()
    data = _load_json(path, "providers.json")
    result: dict[str, dict[str, Provider]] = {}
    for agent, group in data.items():
        if not isinstance(group, dict):
            die(f"agent group '{agent}' must be an object")
        agent_providers: dict[str, Provider] = {}
        for name, entry in group.items():
            if not isinstance(entry, dict):
                die(f"provider '{agent}.{name}' must be an object")
            base_url = entry.get("base_url")
            auth = entry.get("auth")
            if not base_url or not auth:
                die(f"provider '{agent}.{name}' missing base_url or auth")
            auth_header = entry.get("auth_header") or detect_auth_header(base_url)
            agent_providers[name] = Provider(
                name=name, base_url=base_url, auth=auth, auth_header=auth_header,
            )
        result[agent] = agent_providers
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
    )

    profiles: dict[str, RoutingProfile] = {}
    for name, body in data.items():
        if not isinstance(body, dict):
            die(f"profile '{name}' must be an object")
        agent = body.get("agent")
        if not agent:
            die(f"profile '{name}' missing required 'agent' field")
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
            agent=str(agent),
            long_context_threshold=int(body["longContextThreshold"]),
            destinations=parsed,
        )
    return settings, profiles


def resolve_provider(name: str, providers: dict[str, Provider]) -> Provider:
    if name not in providers:
        avail = ", ".join(providers) or "(none)"
        die(f"provider '{name}' not found in this profile's agent group; available: {avail}")
    return providers[name]


def load_for_profile(name: str) -> tuple[dict[str, Provider], RoutingProfile, Settings]:
    """Resolve one profile: returns (agent providers, profile, settings)."""
    providers_all = load_providers()
    settings, profiles = load_routing()
    if name not in profiles:
        avail = ", ".join(profiles) or "(none)"
        die(f"profile '{name}' not found in routing.json; available: {avail}")
    profile = profiles[name]
    if profile.agent not in providers_all:
        avail = ", ".join(providers_all) or "(none)"
        die(f"agent '{profile.agent}' (for profile '{name}') not found in providers.json; available: {avail}")
    agent_providers = providers_all[profile.agent]
    for tier, dest in profile.destinations.items():
        dest.provider = resolve_provider(dest.provider_name, agent_providers)
    return agent_providers, profile, settings


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


# ---------------------------------------------------------------------------
# Config display
# ---------------------------------------------------------------------------

def format_providers_display(all_providers: dict[str, dict[str, Provider]]) -> str:
    display = {}
    for agent, group in all_providers.items():
        agent_display = {}
        for name, p in group.items():
            entry = {"base_url": p.base_url, "auth_header": p.auth_header}
            if ENV_REF_RE.fullmatch(str(p.auth)):
                entry["auth"] = str(p.auth)
            else:
                entry["auth"] = "<set>"
            agent_display[name] = entry
        display[agent] = agent_display
    return json.dumps(display, indent=2)


def format_routing_display(settings: Settings, profiles: dict[str, RoutingProfile]) -> str:
    display = {
        "settings": {
            "backgroundModel": settings.background_model,
            "thinkModel": settings.think_model,
        },
    }
    for name, p in profiles.items():
        display[name] = {
            "agent": p.agent,
            "longContextThreshold": p.long_context_threshold,
            "destinations": {
                k: f"{v.provider_name},{v.model}" for k, v in p.destinations.items()
            },
        }
    return json.dumps(display, indent=2)


# ---------------------------------------------------------------------------
# Click CLI
# ---------------------------------------------------------------------------

@click.group(
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
    click.echo("providers.json:" )
    click.echo(format_providers_display(load_providers()))
    click.echo()
    click.echo("routing.json:")
    click.echo(format_routing_display(*load_routing()))


@config.command("edit")
def config_edit_cmd():
    """Open config dir in $EDITOR."""
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    if not providers_path().exists() or not routing_path().exists():
        die(f"config not found in {d}\nrun: awerouter config init")
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


def main(argv=None):
    try:
        return cli.main(args=argv, prog_name="awerouter")
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
