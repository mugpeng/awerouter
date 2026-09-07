"""CLI commands: serve (run/status/stop) / add / list / usage.

Imports the click group from config.py and extends it. The `config` group
(path/show/edit, plus login/logout/restore) lives in config.py.
"""

import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import click

from awerouter import __version__
from awerouter import runtime
from awerouter import service
from awerouter.config import (
    DEFAULT_PORT,
    ProfileGroup,
    SuggestGroup,
    cli as config_cli,
    config_dir,
    die,
    init_config,
    is_loopback_url,
    load_default_profile,
    load_for_profile,
    load_providers,
    load_routing,
    providers_path,
    routing_path,
    save_profile_entry,
    save_provider,
    validate_profiles,
)
from awerouter.protocols import PROTOCOL_IDS, effective_tokens
from awerouter.server import GATEWAY_PROFILE_NAME, _load_gateway_state, _serve, _serve_gateway
from awerouter.types import AutoThresholdConfig
from awerouter.update_check import _version_gte, get_pypi_latest, skill_refresh_hint

# Attach config sub-group to the main cli group
cli = config_cli


def _resolve_port(cli_port, profile) -> tuple[int, bool]:
    """--port wins over the profile's 'port' field, which wins over the default.

    Returns (port, explicit); an explicit port must not silently move on
    conflict — clients point at it.
    """
    if cli_port is not None:
        return cli_port, True
    if profile.port is not None:
        return profile.port, True
    return DEFAULT_PORT, False


def _run_serve(profile, port, host: str, background: bool = False) -> None:
    if profile:
        providers, routing, settings = load_for_profile(profile)
    else:
        providers, routing, settings = load_default_profile()
    port, port_explicit = _resolve_port(port, routing)
    try:
        asyncio.run(_serve(host, port, providers, routing, settings, port_explicit, background))
    except KeyboardInterrupt:
        raise SystemExit(0)


def _run_serve_gateway(port, host: str, background: bool = False) -> None:
    """One daemon, one port, every profile — model names pick the profile.
    Gateway mode ignores per-profile 'port' fields: its own port is --port or
    the default 20128."""
    if port is None:
        port, port_explicit = DEFAULT_PORT, False
    else:
        port_explicit = True
    try:
        asyncio.run(_serve_gateway(host, port, port_explicit, background))
    except KeyboardInterrupt:
        raise SystemExit(0)


class ServeGroup(ProfileGroup):
    """`serve` group where an unknown subcommand is a profile name, so the
    pre-subcommand spelling `awerouter serve cc-router-1` keeps starting the
    daemon (== `awerouter serve run cc-router-1`)."""

    profile_hint = "awerouter serve run <profile> or awerouter <profile>"


@cli.group(cls=ServeGroup, invoke_without_command=True,
           context_settings={"help_option_names": ["-h", "--help"]})
def serve():
    """Start and manage the awerouter daemon (run / status / stop / restart).

    Bare `awerouter serve` keeps its old meaning: auto-select the profile and
    run in the foreground. Options like -d belong to `serve run`.
    """
    if click.get_current_context().invoked_subcommand is None:
        _run_serve(None, None, "127.0.0.1")


@serve.command()
@click.argument("profile", required=False)
@click.option("--port", default=None, type=int,
              help="Listen port (overrides the profile's 'port'; default 20128).")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("-d", "--background", is_flag=True, default=False,
              help="Detach and keep running after the terminal closes "
                   "(output -> serve-<profile>.log in the state dir).")
@click.option("--install", is_flag=True, default=False,
              help="Run as a resident service instead: starts at login (survives a "
                   "reboot), relaunches after a crash (launchd / systemd user unit). "
                   "Implies -d; re-run to update host/port/env. POSIX only.")
def run(profile, port: int, host: str, background: bool, install: bool):
    """Start the awerouter daemon for PROFILE.

    PROFILE is a profile id from routing.json. If omitted, auto-selects when only
    one profile exists. Config changes (routing.json / providers.json) hot-reload
    without a restart. With -d the daemon runs in the background; with --install
    it runs as a resident service that also starts at login and survives reboots.
    See `awerouter serve status` and `awerouter serve stop`.
    """
    if install:
        _install_background(profile, port, host)
    elif background:
        _start_background(profile, port, host)
    else:
        _run_serve(profile, port, host)


@serve.command("all")
@click.option("--port", default=None, type=int,
              help="Listen port (default 20128; profile 'port' fields do not apply here).")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("-d", "--background", is_flag=True, default=False,
              help="Detach and keep running after the terminal closes "
                   "(output -> serve-gateway.log in the state dir).")
@click.option("--install", is_flag=True, default=False,
              help="Run as a resident service instead: starts at login (survives a "
                   "reboot), relaunches after a crash (launchd / systemd user unit). "
                   "Implies -d; re-run to update host/port/env. POSIX only.")
def serve_all_cmd(port: int, host: str, background: bool, install: bool):
    """Start ONE daemon serving EVERY profile on a single port.

    The request's model name picks the profile: '<profile>/auto|flash|pro'
    (e.g. step-glm/auto — see GET /v1/models). Bare names (auto/flash/pro)
    route to the profile named by routing.json's top-level 'defaultProfile',
    or the only profile when there is just one. Config changes hot-reload
    without a restart; `awerouter serve stop gateway` stops it. With
    --install the gateway runs as a resident service (starts at login).
    """
    if install:
        _install_background(None, port, host, gateway=True)
    elif background:
        _start_background_gateway(port, host)
    else:
        _run_serve_gateway(port, host)


@click.command("__serve_profile__", hidden=True)
@click.option("--port", default=None, type=int,
              help="Listen port (overrides the profile's 'port'; default 20128).")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("-d", "--background", is_flag=True, default=False,
              help="Detach and keep running after the terminal closes.")
@click.pass_context
def _serve_profile(ctx, port: int, host: str, background: bool):
    """Bare profile launch: `awerouter <profile>` and `awerouter serve <profile>`
    == `awerouter serve run <profile>`."""
    _run_serve(ctx.meta["profile_name"], port, host, background)


cli.add_command(_serve_profile)
serve.add_command(_serve_profile)


# How long `serve run -d` waits for the detached child to bind + register
# before giving up and reporting the log tail.
_BG_STARTUP_TIMEOUT_S = 15.0


def _spawn_background(cmd: list, log: Path, name: str) -> dict:
    """Spawn the detached daemon child and wait for its registration.

    Dies with the log tail when it exits or never binds within the timeout.
    Returns the registered instance (pid, port, protocol, ...)."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as out:
        child = subprocess.Popen(
            cmd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True, env=dict(os.environ),
        )
    deadline = time.monotonic() + _BG_STARTUP_TIMEOUT_S
    inst = None
    while time.monotonic() < deadline:
        inst = runtime.instance_by_pid(child.pid)
        if inst is not None:
            break
        if child.poll() is not None:
            break
        time.sleep(0.1)
    if inst is None:
        tail = _log_tail(log)
        die(
            f"background serve for '{name}' failed to start"
            + (f":\n{tail}" if tail else f" (see {log})")
        )
    return inst


def _start_background(profile, port, host: str) -> None:
    if os.name == "nt":
        die("--background is not supported on Windows (POSIX only)")
    if profile:
        _, prof, _ = load_for_profile(profile)
    else:
        _, prof, _ = load_default_profile()
    running = [i for i in runtime.list_instances() if i["profile"] == prof.name]
    if running:
        listed = ", ".join(f"pid {i['pid']} port {i['port']}" for i in running)
        print(f"note: '{prof.name}' is already running ({listed}); starting another anyway")
    log = runtime.serve_log_path(prof.name)
    cmd = [sys.executable, "-m", "awerouter", "__serve_daemon__", prof.name,
           "--host", host]
    if port is not None:
        cmd += ["--port", str(port)]
    inst = _spawn_background(cmd, log, prof.name)
    print(f"awerouter {prof.name} running in background (pid {inst['pid']})")
    print(f"  listening -> {inst['host']}:{inst['port']}  [{inst['protocol']}]")
    print(f"  log       -> {log}")
    print(f"  manage    -> awerouter serve status | awerouter serve stop {prof.name}")


def _start_background_gateway(port, host: str) -> None:
    if os.name == "nt":
        die("--background is not supported on Windows (POSIX only)")
    # Fail fast on a broken config before detaching a doomed child.
    _load_gateway_state()
    running = [i for i in runtime.list_instances() if i["profile"] == GATEWAY_PROFILE_NAME]
    if running:
        listed = ", ".join(f"pid {i['pid']} port {i['port']}" for i in running)
        print(f"note: the gateway is already running ({listed}); starting another anyway")
    log = runtime.serve_log_path(GATEWAY_PROFILE_NAME)
    cmd = [sys.executable, "-m", "awerouter", "__serve_gateway_daemon__", "--host", host]
    if port is not None:
        cmd += ["--port", str(port)]
    inst = _spawn_background(cmd, log, GATEWAY_PROFILE_NAME)
    print(f"awerouter gateway running in background (pid {inst['pid']})")
    print(f"  listening -> {inst['host']}:{inst['port']}  [{inst['protocol']}]")
    print(f"  log       -> {log}")
    print("  manage    -> awerouter serve status | awerouter serve stop gateway")


def _log_tail(log: Path, lines: int = 15) -> str:
    try:
        return "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def _install_service(prof_name: str, provider_refs: list, cmd: list) -> None:
    """Install core shared by `--install` and `serve restart`: stop the
    target's old instances, write the service file with freshly collected
    env, start it, wait for its registration."""
    service.require_env(provider_refs)
    environment = service.collect_env()
    # The service owns the target now: stop plain instances (same-port conflict)
    # and any previously installed job before installing the fresh file.
    replaced = runtime.stop_instances(prof_name, services=False)
    service.stop(prof_name)
    for inst in replaced:
        print(f"note: stopped running {prof_name} (pid {inst['pid']}) — the service replaces it")
    log = runtime.serve_log_path(prof_name)
    path = service.install(prof_name, cmd, log, environment)
    inst = service.wait_registered(prof_name, _BG_STARTUP_TIMEOUT_S)
    if inst is None:
        tail = _log_tail(log)
        die(
            f"resident service for '{prof_name}' failed to start"
            + (f":\n{tail}" if tail else f" (see {log})")
        )
    print(f"awerouter {prof_name} running as a resident service "
          f"({service.service_kind()}, pid {inst['pid']})")
    print(f"  listening -> {inst['host']}:{inst['port']}  [{inst['protocol']}]")
    print(f"  log       -> {log}")
    print(f"  service   -> {path}  (0600; starts at login, relaunches after a crash)")
    if environment:
        print(f"  env       -> {len(environment)} var(s) baked into the service file "
              "(re-run --install after changing them)")
    print(f"  manage    -> awerouter serve status | awerouter serve stop {prof_name} "
          f"(stops until next login) | awerouter serve stop {prof_name} --purge (removes)")


def _install_background(profile, port, host: str, gateway: bool = False) -> None:
    """`serve run/all --install`: resident service via launchd/systemd.

    The service replaces any running instance of the same target first (one
    port, one owner), then installs the job and waits for its registration —
    same contract as `-d` plus start-at-login and crash relaunch."""
    if os.name == "nt":
        die("--install is not supported on Windows (POSIX only)")
    # Fail fast on a broken config / missing ${VAR} before writing anything.
    if gateway:
        _load_gateway_state()
        prof_name = GATEWAY_PROFILE_NAME
        provider_refs = [(group, name) for group, provs in load_providers().items()
                         for name in provs]
        cmd = [sys.executable, "-m", "awerouter", "__serve_gateway_daemon__", "--host", host]
    else:
        if profile:
            _, prof, _ = load_for_profile(profile)
        else:
            _, prof, _ = load_default_profile()
        prof_name = prof.name
        provider_refs = [(group, dest.provider_name) for group in prof.protocols
                         for dest in prof.destinations.values()]
        cmd = [sys.executable, "-m", "awerouter", "__serve_daemon__", prof_name,
               "--host", host]
    if port is not None:
        cmd += ["--port", str(port)]
    _install_service(prof_name, provider_refs, cmd)


@click.command("__serve_daemon__", hidden=True)
@click.argument("profile")
@click.option("--port", default=None, type=int,
              help="Listen port (overrides the profile's 'port'; default 20128).")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
def _serve_daemon(profile, port: int, host: str):
    """Foreground child of `serve run --background` (registered as background)."""
    try:
        sys.stdout.reconfigure(line_buffering=True)  # log file is block-buffered otherwise
    except (AttributeError, ValueError):  # non-tty/stdout replacement without reconfigure
        pass
    _run_serve(profile, port, host, background=True)


cli.add_command(_serve_daemon)


@click.command("__serve_gateway_daemon__", hidden=True)
@click.option("--port", default=None, type=int,
              help="Listen port (default 20128).")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
def _serve_gateway_daemon(port: int, host: str):
    """Foreground child of `serve all --background` (registered as background)."""
    try:
        sys.stdout.reconfigure(line_buffering=True)  # log file is block-buffered otherwise
    except (AttributeError, ValueError):  # non-tty/stdout replacement without reconfigure
        pass
    _run_serve_gateway(port, host, background=True)


cli.add_command(_serve_gateway_daemon)


def _fmt_uptime(started) -> str:
    if not isinstance(started, (int, float)):
        return "-"
    s = max(0, int(time.time() - started))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h{m:02d}m"
    d, h = divmod(h, 24)
    return f"{d}d{h}h"


@serve.command("status")
def status_cmd():
    """Show running serve instances (foreground, background, resident)."""
    instances = runtime.list_instances()
    idle = [s for s in service.installed_services()
            if not any(i["profile"] == s["name"] for i in instances)]
    if not instances and not idle:
        click.echo("(no running instances)")
        click.echo("start one: awerouter serve run <profile>   (add -d to run in the background)")
        return
    for inst in sorted(instances, key=lambda i: (i["profile"], i["port"])):
        if inst.get("service"):
            mode = f"svc:{inst['service']}"
        elif inst.get("background"):
            mode = "bg"
        else:
            mode = "fg"
        click.echo(
            f"{inst.get('profile', '?')}\t{mode}\tpid {inst.get('pid', '?')}\t"
            f"{inst.get('host', '?')}:{inst.get('port', '?')}\t"
            f"[{inst.get('protocol', '?')}]\tup {_fmt_uptime(inst.get('started'))}"
        )
    for s in idle:
        click.echo(
            f"{s['name']}\tsvc:{s['kind']}\t(resident service installed — not running; "
            f"starts at login, remove: awerouter serve stop {s['name']} --purge)"
        )


@serve.command("stop")
@click.argument("profile", required=False)
@click.option("--purge", is_flag=True, default=False,
              help="Also remove the resident service (--install) so it no longer "
                   "starts at login; without it a stopped service returns at next login.")
def stop_cmd(profile, purge):
    """Stop running serve instances (all of them, or PROFILE's only).

    Resident-service instances are stopped through the service manager (a
    plain kill would be undone by the restart policy) and return at the next
    login; --purge also removes them from startup."""
    if os.name == "nt":
        die("stop is not supported on Windows (POSIX only)")
    stopped = runtime.stop_instances(profile, services=False)
    for inst in stopped:
        line = f"stopped {inst['profile']} (pid {inst['pid']}, port {inst['port']})"
        if runtime.pid_alive(inst["pid"]):
            line += " — still shutting down"
        click.echo(line)
    svc_stopped = []
    for inst in (i for i in runtime.list_instances()
                 if (profile is None or i["profile"] == profile) and i.get("service")):
        service.stop(inst["profile"])
        for _ in range(20):
            if not runtime.pid_alive(inst["pid"]):
                break
            time.sleep(0.1)
        click.echo(
            f"stopped {inst['profile']} (pid {inst['pid']}, port {inst['port']}) "
            "[resident — returns at next login; --purge removes it]"
        )
        svc_stopped.append(inst["profile"])
    purged = []
    if purge:
        names = set(svc_stopped)
        if profile is not None:
            names.add(profile)
        else:
            names |= {s["name"] for s in service.installed_services()}
        for name in sorted(names):
            removed = service.purge(name)
            if removed:
                click.echo(f"removed resident service for {name} ({removed}) — "
                           "no longer starts at login")
                purged.append(name)
    if not stopped and not svc_stopped:
        if profile:
            click.echo(f"(nothing to stop — no running instance for profile '{profile}')")
        else:
            click.echo("(nothing to stop)")
    if purge and not purged and not svc_stopped:
        click.echo("(no resident service installed)")


def _restart_resident(name: str) -> None:
    """Re-install the resident service for name: same command line, port and
    host as before, env re-baked from the current shell (new secrets apply)."""
    cmd = service.read_cmd(name)
    if not cmd:
        die(
            f"no daemon command line found in the installed service for '{name}'\n"
            f"fix: remove it (awerouter serve stop {name} --purge) and re-run "
            f"awerouter serve run {name} --install"
        )
    if name == GATEWAY_PROFILE_NAME:
        _load_gateway_state()
        provider_refs = [(group, prov) for group, provs in load_providers().items()
                         for prov in provs]
    else:
        _, prof, _ = load_for_profile(name)
        provider_refs = [(group, dest.provider_name) for group in prof.protocols
                         for dest in prof.destinations.values()]
    click.echo(f"restarting {name} as a resident service (env re-baked from this shell)")
    _install_service(name, provider_refs, cmd)


@serve.command("restart")
@click.argument("profile", required=False)
def restart_cmd(profile):
    """Restart serve instances (all of them, or PROFILE's only).

    The way to apply a changed environment variable or secret: resident
    services are re-installed from the current shell (same command line, port
    and host, env baked fresh); background instances are stopped and
    re-spawned from the current shell; foreground instances belong to their
    own terminal and are skipped."""
    if os.name == "nt":
        die("restart is not supported on Windows (POSIX only)")
    instances = runtime.list_instances()
    svc_names = {s["name"] for s in service.installed_services()}
    names = {i["profile"] for i in instances} | svc_names
    if profile is not None:
        names = {n for n in names if n == profile}
    if not names:
        if profile:
            click.echo(f"(nothing to restart — no running instance or resident "
                       f"service for profile '{profile}')")
        else:
            click.echo("(nothing to restart)")
        return
    for name in sorted(names):
        if name in svc_names:
            _restart_resident(name)
            continue
        insts = [i for i in instances if i["profile"] == name]
        bg = [i for i in insts if i.get("background")]
        if bg:
            runtime.stop_instances(name, services=False)
            click.echo(f"restarting {name} in the background "
                       f"(same port {bg[0]['port']})")
            _start_background(name, bg[0]["port"], bg[0].get("host", "127.0.0.1"))
        for inst in (i for i in insts if not i.get("background")):
            click.echo(
                f"note: {name} (pid {inst['pid']}) is a foreground instance — "
                "restart it in its own terminal (Ctrl-C, then serve run)"
            )


_NEW_PROVIDER = "<new>"


@cli.command("add")
def add():
    """Interactively add a routing profile (creates any new providers)."""
    if not providers_path().exists() or not routing_path().exists():
        init_config()
        click.echo(f"initialized config in {config_dir()}")
    providers_all = load_providers()
    _, profiles = load_routing()

    name = click.prompt("Profile name")
    if name in profiles:
        die(f"profile already exists: {name}")

    click.echo("providers.json categories:")
    for pid in PROTOCOL_IDS:
        names = ", ".join(sorted(providers_all.get(pid, {}))) or "(empty)"
        click.echo(f"  {pid:18s} {names}")
    protocol = click.prompt("Protocol (providers.json category)",
                            type=click.Choice(PROTOCOL_IDS), default="anthropic")
    known = set(providers_all.get(protocol, {}))

    def ask_tier(tier: str) -> str:
        pname = None
        if known:
            choices = sorted(known) + [_NEW_PROVIDER]
            picked = click.prompt(f"{tier} provider", type=click.Choice(choices))
            if picked != _NEW_PROVIDER:
                pname = picked
        if pname is None:
            pname = click.prompt("  new provider name")
            base_url = click.prompt(f"  {pname} base_url")
            auth_var = click.prompt(
                f"  {pname} auth env var name (empty for local / no-auth)", default=""
            )
            if auth_var:
                save_provider(protocol, pname, base_url, f"${{{auth_var}}}")
            else:
                if not is_loopback_url(base_url) and not click.confirm(
                    f"  {pname} is off-machine and has no auth — cloud APIs need one. "
                    "Add anyway?",
                    default=False,
                ):
                    die("aborted — rerun 'awerouter add' to retry")
                save_provider(protocol, pname, base_url, None)
            known.add(pname)
        model = click.prompt(f"{tier} model id")
        return f"{pname},{model}"

    flash = ask_tier("flash")
    pro = ask_tier("pro")
    threshold_raw = click.prompt(
        "longContextThreshold (integer, or 'auto' to calibrate from this profile's traffic)",
        default="8000",
    )
    if threshold_raw.strip().lower() == "auto":
        threshold = "auto"
    else:
        try:
            threshold = int(threshold_raw)
        except ValueError:
            die(f"longContextThreshold must be an integer or 'auto', got: {threshold_raw!r}")
    save_profile_entry(name, protocol, threshold, flash, pro)

    # Fail loudly if the wizard wrote something inconsistent.
    validate_profiles(load_providers(), load_routing()[1])
    click.echo(f"Profile '{name}' added: flash={flash}  pro={pro}  L3>{threshold}")
    click.echo(f"Start it with: awerouter {name}")


@cli.command("self-update")
@click.option("--check", "check_only", is_flag=True, help="Show versions without updating.")
def self_update(check_only):
    """Update awerouter to the latest PyPI version."""
    try:
        latest = get_pypi_latest()
    except Exception as e:
        raise SystemExit(f"Failed to check PyPI: {e}")
    if _version_gte(__version__, latest):
        click.echo(f"awerouter is up to date ({__version__}).")
        return
    click.echo(f"Current: {__version__}  Latest: {latest}")
    if check_only:
        return

    if Path(sys.prefix, "pyvenv.cfg").exists() and "pipx" in sys.prefix:
        cmd = [shutil.which("pipx") or "pipx", "upgrade", "awerouter"]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "awerouter"]

    click.echo(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode == 0:
        click.echo("Done. Restart awerouter (including running serve instances) to use the new version.")
        hint = skill_refresh_hint()
        if hint:
            click.echo(hint)
    else:
        raise SystemExit(result.returncode)


@cli.command("list")
def list_profiles():
    """List routing profiles (name, protocol, port, flash, pro, threshold)."""
    providers_all = load_providers()
    _, profiles = load_routing()
    validate_profiles(providers_all, profiles)
    for name, p in profiles.items():
        flash = p.destinations["flash"]
        pro = p.destinations["pro"]
        threshold = "auto" if p.threshold_auto else p.long_context_threshold
        click.echo(
            f"{name}\t{p.protocol}\t{p.port or '-'}\t{flash.provider_name}/{flash.model}"
            f"\t{pro.provider_name}/{pro.model}\tL3>{threshold}"
        )


def _passes_log(entry, cutoff, profile_name) -> bool:
    if cutoff is not None:
        ts = entry.ts
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt < cutoff:
                return False
        except (ValueError, TypeError):
            return False
    if profile_name is not None and (entry.profile or "(unknown)") != profile_name:
        return False
    return True


# Compact per-type labels for `usage log --tokens` lines.
_TOKEN_SHORT = {
    "messages": "msg",
    "system": "sys",
    "tool_results": "results",
    "tool_calls": "calls",
    "thinking": "think",
}


def _settings_or_default(profile_name=None):
    """Loaded routing settings, or None — usage views must not require
    routing.json to exist. With a --profile filter, that profile's effective
    settings (its overrides applied), so the discount/auto knobs match what
    actually routed."""
    try:
        settings, profiles = load_routing()
    except SystemExit:
        return None
    if profile_name and profile_name in profiles:
        return profiles[profile_name].settings
    return settings


def _usage_header(since, profile_name):
    """Print search-discount context for the filtered window."""
    from awerouter.logging import awecompress_totals, odcp_totals, rtk_totals, tail as _tail
    settings = _settings_or_default(profile_name)
    discount = settings.search_result_discount if settings else 0.3
    cutoff = _parse_since(since) if since else None
    entries = [e for e in _tail(None) if _passes_log(e, cutoff, profile_name)]
    if not entries:
        return
    total = sum(e.token_count for e in entries)
    fs = sum(e.file_search_tokens for e in entries)
    eff = effective_tokens(total, fs, discount)
    if fs == 0:
        click.echo(f"search discount: {discount:.0%}  |  total: {total:,}  |  search: 0")
    else:
        click.echo(f"search discount: {discount:.0%}  |  total: {total:,}  |  search: {fs:,}  |  L3 effective: {eff:,}")
    rtk = rtk_totals(cutoff, profile_name)
    if rtk["saved"]:
        click.echo(f"rtk: saved {rtk['saved']:,} input tokens "
                   f"({rtk['requests']}/{len(entries)} requests compressed)")
    odcp = odcp_totals(cutoff, profile_name)
    if odcp["saved"]:
        click.echo(f"odcp: saved {odcp['saved']:,} input tokens "
                   f"({odcp['requests']}/{len(entries)} requests pruned)")
    awc = awecompress_totals(cutoff, profile_name)
    if awc["saved"]:
        click.echo(f"awecompress: saved {awc['saved']:,} input tokens "
                   f"({awc['requests']}/{len(entries)} requests compressed)")


def _usage_log(n, since=None, profile_name=None, tokens_mode=False):
    from awerouter.logging import tail as _tail
    # With a window filter, read the whole log and filter FIRST, then take the
    # last n — otherwise matches outside the raw last-n window never show.
    if since or profile_name:
        cutoff = _parse_since(since) if since else None
        entries = [e for e in _tail(None) if _passes_log(e, cutoff, profile_name)]
        if n:
            entries = entries[-n:]
    else:
        entries = _tail(n)  # n is None => whole file
    if not entries:
        click.echo("(no logs yet)")
        return
    for e in entries:
        head = (
            f"{e.ts}  {e.request_id[:12]:12s}  {e.protocol or '-':16s}  "
            f"{e.agent or '-':12s}  {e.destination:7s}  "
            f"{e.provider:12s}  {e.model_out:24s}  {e.label:14s}  "
        )
        if tokens_mode:
            parts = []
            for k, v in e.tokens.items():
                short = _TOKEN_SHORT.get(k, k)
                if k == "tool_results" and e.file_search_tokens:
                    parts.append(f"{short}={v}(search={e.file_search_tokens})")
                else:
                    parts.append(f"{short}={v}")
            detail = " ".join(parts) or "-"
            click.echo(f"{head}tokens={e.token_count}  {detail}")
            continue
        status_s = str(e.status) if e.status is not None else "-"
        dur_s = f"/{_fmt_ms(e.duration_ms)}" if e.duration_ms else ""
        rtk_s = f"  rtk=+{e.rtk_saved:,}" if e.rtk_saved else ""
        odcp_s = f"  odcp=+{e.odcp_saved:,}" if e.odcp_saved else ""
        retry_s = "  401-retry" if e.codex_retried else ""
        click.echo(
            f"{head}status={status_s:>3}{retry_s}  {_fmt_ms(e.ms)}{dur_s}  "
            f"tokens={e.token_count}  in={e.model_in}{rtk_s}{odcp_s}"
        )


def _parse_since(value: str):
    """Resolve a --since value to an aware local datetime (window lower bound).

    Accepts 'today', 'yesterday', 'Nd' (e.g. 7d), or a date (YYYY-MM-DD).
    """
    import re
    from datetime import datetime, timedelta
    v = value.strip().lower()
    now = datetime.now().astimezone()
    if v == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if v == "yesterday":
        return now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    m = re.fullmatch(r"(\d+)d", v)
    if m:
        return now - timedelta(days=int(m.group(1)))
    try:
        d = datetime.fromisoformat(value)
    except ValueError:
        raise click.BadParameter(
            "expected 'today', 'yesterday', Nd (e.g. 7d), or YYYY-MM-DD"
        ) from None
    return d.astimezone()


def _fmt_ms(ms) -> str:
    if ms is None:
        return "-"
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms}ms"


def _echo_counts(counts: dict, total: int) -> None:
    for k, v in sorted(counts.items()):
        pct = round(100 * v / total) if total else 0
        click.echo(f"    {k:24s} {v} ({pct}%)")


def _lat_suffix(entry) -> str:
    if not entry:
        return ""
    s = f"  p50 {_fmt_ms(entry['p50'])}  p95 {_fmt_ms(entry['p95'])}"
    if "total_p50" in entry:
        s += f"   total p50 {_fmt_ms(entry['total_p50'])}  p95 {_fmt_ms(entry['total_p95'])}"
    return s


def _window_cutoff(since, profile_name):
    """Echo the active window/profile (and coverage floor); return the cutoff."""
    cutoff = _parse_since(since) if since else None
    if since:
        click.echo(f"window         : since {cutoff:%Y-%m-%d %H:%M} local")
        from awerouter.logging import log_start
        start = log_start()
        if start and start > cutoff:
            click.echo(
                f"note           : log starts {start:%Y-%m-%d %H:%M} UTC — older data rotated away"
            )
    if profile_name:
        click.echo(f"profile        : {profile_name}")
    return cutoff


# Window options live on the subcommands that consume them (log/stats/calibrate/
# savings); `clean` deletes the whole log and takes none.
_since_opt = click.option("--since", default=None,
                          help="Count entries from this point on: 'today', 'yesterday', Nd (e.g. 7d), or YYYY-MM-DD.")
_profile_opt = click.option("--profile", "profile_name", default=None,
                            help="Count entries for one routing profile only.")


@cli.group(cls=SuggestGroup)
def usage():
    """Usage analytics over the request log."""


@usage.command()
@_since_opt
@_profile_opt
@click.option("--lines", default=20, show_default=True, help="Number of trailing entries to show.")
@click.option("--all", "show_all", is_flag=True, default=False,
              help="Show every entry instead of the last --lines.")
@click.option("--tokens", "tokens_mode", is_flag=True, default=False,
              help="Show per-type token columns instead of status/latency/model-in.")
def log(lines: int, show_all: bool, tokens_mode: bool, since, profile_name):
    """Show request log entries verbatim (last 20, or --all).

    Last --lines by default; --all shows every entry.
    """
    _usage_header(since, profile_name)
    _usage_log(None if show_all else lines, since, profile_name, tokens_mode)


@usage.command()
@_since_opt
@_profile_opt
def stats(since, profile_name):
    """Routing summary, grouped by profile."""
    _usage_header(since, profile_name)
    _usage_stats(since, profile_name)


_TOKEN_TYPE_ORDER = ("messages", "system", "tools", "tool_results", "tool_calls", "thinking")


@usage.command()
@_since_opt
@_profile_opt
def tokens(since, profile_name):
    """Input-token totals by content type (messages, system, tools, tool I/O)."""
    _usage_header(since, profile_name)
    _usage_tokens(since, profile_name)


@usage.command()
def clean():
    """Delete saved request logs (requests.jsonl + rotated backup)."""
    from awerouter.logging import clear_logs
    if click.confirm("Delete all saved request logs (requests.jsonl + rotated backup)?"):
        removed = clear_logs()
        for p in removed:
            click.echo(f"removed {p}")
        if not removed:
            click.echo("(no logs to remove)")
    else:
        click.echo("aborted")


def _usage_stats(since, profile_name):
    from awerouter.logging import stats as collect_stats
    cutoff = _window_cutoff(since, profile_name)
    s = collect_stats(cutoff, profile_name)
    if not s:
        click.echo("(no logs yet)")
        return
    click.echo(f"total_requests : {s['total_requests']}")
    click.echo(f"~total_tokens  : {s['total_tokens']}  (all request content: messages, system prompt, tools, tool I/O)")
    err_pct = round(100 * s["errors"] / s["total_requests"]) if s["total_requests"] else 0
    click.echo(f"errors         : {s['errors']} ({err_pct}%)")
    click.echo(f"fallbacks      : {s['fallbacks']}  (flash failed -> pro)")
    if s["flash_requests"]:
        click.echo(
            f"pro input offloaded to flash: ~{s['flash_tokens']} tokens "
            f"across {s['flash_requests']} requests"
        )
        click.echo("  (input-side estimate; output tokens are not visible to the proxy)")
    for name, p in sorted(s["by_profile"].items()):
        click.echo()
        extras = (f", {p['errors']} error{'s' if p['errors'] != 1 else ''}"
                  f", {p['fallbacks']} fallback{'s' if p['fallbacks'] != 1 else ''}")
        proto_s = f" [{p['protocol']}]" if p.get("protocol") else ""
        click.echo(f"profile {name}{proto_s}  ({p['requests']} requests, ~{p['flash_tokens']} flash tokens{extras}):")
        lat = p["latency"]
        click.echo("  by_label:")
        _echo_counts(p["by_label"], p["requests"])
        click.echo("  by_agent:")
        _echo_counts(p["by_agent"], p["requests"])
        click.echo("  by_destination:")
        for k, v in sorted(p["by_destination"].items()):
            pct = round(100 * v / p["requests"]) if p["requests"] else 0
            click.echo(f"    {k:24s} {v} ({pct}%){_lat_suffix(lat.get('destination', {}).get(k))}")
        click.echo("  by_provider:")
        for k, v in sorted(p["by_provider"].items()):
            pct = round(100 * v / p["requests"]) if p["requests"] else 0
            click.echo(f"    {k:24s} {v} ({pct}%){_lat_suffix(lat.get('provider', {}).get(k))}")
        click.echo("  by_model:")
        for k, v in sorted(p["by_model"].items()):
            pct = round(100 * v / p["requests"]) if p["requests"] else 0
            click.echo(f"    {k:24s} {v} ({pct}%){_lat_suffix(lat.get('model', {}).get(k))}")


def _usage_tokens(since, profile_name):
    from awerouter.logging import token_breakdown
    cutoff = _window_cutoff(since, profile_name)
    b = token_breakdown(cutoff, profile_name)
    if not b:
        click.echo("(no logs yet)")
        return
    n, total = b["requests"], b["total"]
    settings = _settings_or_default(profile_name)
    discount = settings.search_result_discount if settings else 0.3
    fs = b.get("file_search_tokens", 0)
    eff = effective_tokens(total, fs, discount)
    click.echo(f"input tokens by type ({n} requests, total {total:,}  search {fs:,}  effective {eff:,}):")
    keys = [k for k in _TOKEN_TYPE_ORDER if k in b["by_type"]]
    keys += [k for k in b["by_type"] if k not in _TOKEN_TYPE_ORDER]
    for k in keys:
        v = b["by_type"][k]
        pct = round(100 * v / total) if total else 0
        if k == "tool_results" and fs:
            click.echo(f"  {k:13s} {v:>11,}  {pct:>3}%  avg {v // max(n, 1):,}/req"
                       f"  (includes {fs} search at {discount:.0%} weight)")
        else:
            click.echo(f"  {k:13s} {v:>11,}  {pct:>3}%  avg {v // max(n, 1):,}/req")
    if b["legacy_requests"]:
        click.echo(
            f"  ({b['legacy_requests']} pre-breakdown entries, "
            f"{b['legacy_tokens']:,} tokens not itemized)"
        )


@usage.command()
@_since_opt
@_profile_opt
def calibrate(since, profile_name):
    """Tune longContextThreshold from the L3 token distribution.

    Only threshold-sensitive traffic counts (default/longContext/image
    labels, plus toolSearch from pre-v0.4.8 logs); webSearch,
    background/think, and toolEdit route identically regardless of where
    the threshold sits.
    """
    from awerouter.logging import auto_threshold, token_distribution
    cutoff = _window_cutoff(since, profile_name)
    settings = _settings_or_default(profile_name)
    discount = settings.search_result_discount if settings else 0.3
    d = token_distribution(cutoff, profile_name, discount)
    if not d:
        click.echo("(no L3 traffic yet — run some non-background/think requests first)")
        return
    click.echo(f"L3 request-token distribution ({d['n']} requests):")
    click.echo("  (all request content: messages, system prompt, tool definitions, tool I/O)")
    click.echo(f"  (file-search tool results weighed at {discount:.0%})")
    click.echo(f"  min: {d['min']:>7}   p50: {d['p50']:>7}   p75: {d['p75']:>7}")
    click.echo(f"  p90: {d['p90']:>7}   p95: {d['p95']:>7}   p99: {d['p99']:>7}   max: {d['max']:>7}")
    click.echo()
    click.echo("if you set longContextThreshold to:")
    for c in d["candidates"]:
        click.echo(f"  {c['threshold']:>7}   → {c['flash_pct']}% flash, {100 - c['flash_pct']}% pro")
    # The auto policy uses its own trailing window (settings.longContextAuto),
    # independent of the --since view above.
    cfg = settings.long_context_auto if settings else AutoThresholdConfig()
    picked = auto_threshold(profile_name, discount, cfg)
    click.echo()
    if picked is not None:
        threshold, n = picked
        click.echo(f"'auto' would set: {threshold:,}  "
                   f"(p{cfg.percentile} of {n} L3 requests, last {cfg.window_days}d)")
    else:
        click.echo(f"'auto': fewer than {cfg.min_samples} L3 requests in last {cfg.window_days}d "
                   f"— would use fallbackThreshold {cfg.fallback_threshold:,}")


# Anthropic-style cache economics for the savings bracket (price multipliers,
# not prices — users apply their own per-token prices).
_CACHE_READ_FACTOR = 0.1
_CACHE_WRITE_FACTOR = 1.25


@usage.command()
@_since_opt
@_profile_opt
def savings(since, profile_name):
    """Token accounting vs a pro-only setup (token view, no prices)."""
    _usage_header(since, profile_name)
    _usage_savings(since, profile_name)


def _usage_savings(since, profile_name):
    from awerouter.logging import awecompress_totals, cadence, odcp_totals, rtk_totals, token_totals
    cutoff = _window_cutoff(since, profile_name)
    t = token_totals(cutoff, profile_name)
    if not t:
        click.echo("(no logs yet)")
        return
    flash, pro = t["flash"], t["pro"]
    total_req = flash["requests"] + pro["requests"]
    total_tok = flash["tokens"] + pro["tokens"]
    offloaded = flash["tokens"]
    pct_tok = round(100 * offloaded / total_tok) if total_tok else 0
    pct_req = round(100 * flash["requests"] / total_req) if total_req else 0

    click.echo(f"requests: {total_req}  (flash {flash['requests']} / pro {pro['requests']}, "
               f"{pct_req}% flash, fallback {t['fallback']})")
    click.echo()
    click.echo("request input tokens (input side only — output tokens are not visible to the proxy):")
    click.echo(f"  flash   {flash['tokens']:>9,}   avg {flash['tokens'] // max(flash['requests'], 1):,}/req")
    click.echo(f"  pro     {pro['tokens']:>9,}   avg {pro['tokens'] // max(pro['requests'], 1):,}/req")
    click.echo(f"  total   {total_tok:>9,}")

    rtk = rtk_totals(cutoff, profile_name)
    if rtk["saved"]:
        click.echo()
        click.echo("rtk compression (input trimmed before billing, stacks with flash offload):")
        click.echo(f"  saved {rtk['saved']:,} input tokens across {rtk['requests']} requests")
    odcp = odcp_totals(cutoff, profile_name)
    if odcp["saved"]:
        click.echo()
        click.echo("odcp pruning (superseded tool-call content dropped, stacks with the above):")
        click.echo(f"  saved {odcp['saved']:,} input tokens across {odcp['requests']} requests")
    awc = awecompress_totals(cutoff, profile_name)
    if awc["saved"]:
        click.echo()
        click.echo("awecompress (oldest turns folded into frozen summaries, stacks with the above):")
        click.echo(f"  saved {awc['saved']:,} input tokens across {awc['requests']} requests")
    click.echo()
    click.echo("vs a pro-only setup:")
    click.echo(f"  pro input billed   {total_tok:,} → {pro['tokens']:,}")
    click.echo(f"  offloaded to flash {offloaded:,}  ({pct_tok}% of input tokens)")

    c = cadence(cutoff, profile_name)
    lower = None
    if c and c["requests"] > 1 and offloaded:
        lower = round(offloaded * _CACHE_READ_FACTOR)
        click.echo()
        click.echo(f"cache sensitivity (Anthropic-style: read ~{_CACHE_READ_FACTOR:.0%}, "
                   f"write ~{_CACHE_WRITE_FACTOR:.0%}, TTL {c['ttl_s'] // 60} min):")
        click.echo(f"  flash<->pro alternations: {c['alternations']}")
        click.echo(f"  consecutive-pro gaps: {c['pro_gaps']} "
                   f"({c['pro_gaps'] - c['pro_gaps_expired']} within TTL, {c['pro_gaps_expired']} expired)")
        click.echo(f"  all-request gaps expired: {c['all_gaps_expired']}/{c['all_gaps']}"
                   "  (each would re-warm pro's cache in a pro-only world)")
        click.echo(f"  offload worth {lower:,}–{offloaded:,} pro-equivalent input tokens")
        click.echo("  (lower = all would have been cache reads; a cache-warm pro-only baseline sits near it)")

    click.echo()
    if offloaded:
        click.echo("plug in your input prices (per 1M tokens) to get money saved:")
        click.echo(f"  upper       = ({offloaded:,} × pro − {offloaded:,} × flash) / 1,000,000")
        if lower is not None:
            click.echo(f"  cache-aware = ({lower:,} × pro − {offloaded:,} × flash) / 1,000,000")
            click.echo("  (cache-aware assumes the pro-only baseline billed the offload as ~10% cache reads)")
        click.echo("flash-side caching (would lower flash cost) and capability-mismatch turns are not modeled")


def main(argv=None):
    from awerouter.update_check import check_async
    get_reminder = check_async(sys.argv[1:] if argv is None else argv)
    try:
        return cli.main(args=argv, prog_name="awerouter")
    finally:
        reminder = get_reminder()
        if reminder:
            click.echo(f"⚠  {reminder}", err=True)


if __name__ == "__main__":
    raise SystemExit(main())
