import json
import os
import re
import shutil
from pathlib import Path
from typing import Optional

import click

from awerouter import __version__
from awerouter.types import Destination, Provider, RoutingConfig

# ---------------------------------------------------------------------------
# Constants (mirror aweswitch cli.py conventions exactly)
# ---------------------------------------------------------------------------

ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
SECRET_RE = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD|AUTH)", re.IGNORECASE)

TEMPLATE_PROVIDERS = Path(__file__).parent / "default-providers.json"
TEMPLATE_ROUTING = Path(__file__).parent / "default-routing.json"


def die(message: str) -> "SystemExit":
    raise SystemExit(f"awerouter: {message}")


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


def load_providers(path: Optional[Path] = None) -> dict[str, Provider]:
    path = path or providers_path()
    data = _load_json(path, "providers.json")
    result: dict[str, Provider] = {}
    for name, entry in data.items():
        if not isinstance(entry, dict):
            die(f"provider '{name}' must be an object")
        base_url = entry.get("base_url")
        auth = entry.get("auth")
        if not base_url or not auth:
            die(f"provider '{name}' missing base_url or auth")
        result[name] = Provider(
            name=name,
            base_url=base_url,
            auth=auth,
            auth_header=entry.get("auth_header", "authorization"),
        )
    return result


def load_routing(path: Optional[Path] = None) -> RoutingConfig:
    path = path or routing_path()
    data = _load_json(path, "routing.json")
    required = ["backgroundModel", "thinkModel", "longContextThreshold", "destinations"]
    for key in required:
        if key not in data:
            die(f"routing.json missing required key: {key}")
    dests = data.get("destinations", {})
    if not isinstance(dests, dict):
        die("routing.json destinations must be an object")
    parsed: dict[str, Destination] = {}
    for tier, raw in dests.items():
        if tier not in ("flash", "pro"):
            die(f"routing.json destination key must be flash or pro, got: {tier}")
        parsed[tier] = _parse_destination(str(raw))
    return RoutingConfig(
        background_model=str(data["backgroundModel"]),
        think_model=str(data["thinkModel"]),
        long_context_threshold=int(data["longContextThreshold"]),
        destinations=parsed,
    )


def resolve_provider(name: str, providers: dict[str, Provider]) -> Provider:
    if name not in providers:
        die(f"provider '{name}' referenced in routing.json not found in providers.json")
    return providers[name]


def load_config() -> tuple[dict[str, Provider], RoutingConfig]:
    providers = load_providers()
    routing = load_routing()
    # Validate destination provider references and attach Provider objects
    for tier, dest in routing.destinations.items():
        dest.provider = resolve_provider(dest.provider_name, providers)
    return providers, routing


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

def format_providers_display(providers: dict[str, Provider]) -> str:
    display = {}
    for name, p in providers.items():
        entry = {
            "base_url": p.base_url,
            "auth_header": p.auth_header,
        }
        auth_raw = p.auth
        if ENV_REF_RE.fullmatch(str(auth_raw)):
            entry["auth"] = str(auth_raw)
        else:
            entry["auth"] = "<set>"
        display[name] = entry
    return json.dumps(display, indent=2)


def format_routing_display(routing: RoutingConfig) -> str:
    return json.dumps({
        "backgroundModel": routing.background_model,
        "thinkModel": routing.think_model,
        "longContextThreshold": routing.long_context_threshold,
        "destinations": {
            k: f"{v.provider_name},{v.model}"
            for k, v in routing.destinations.items()
        },
    }, indent=2)


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
    click.echo(format_routing_display(load_routing()))


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
