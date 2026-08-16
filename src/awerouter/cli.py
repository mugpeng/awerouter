"""CLI commands: serve / add / list / show / log / stats / calibrate.

Imports the click group from config.py and extends it.
"""

import asyncio

import click

from awerouter.config import (
    cli as config_cli,
    config_dir,
    die,
    format_providers_display,
    format_routing_display,
    init_config,
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
from awerouter.protocols import PROTOCOL_IDS
from awerouter.server import _serve

# Attach config sub-group to the main cli group
cli = config_cli


def _run_serve(profile, port: int, host: str) -> None:
    if profile:
        providers, routing, settings = load_for_profile(profile)
    else:
        providers, routing, settings = load_default_profile()
    try:
        asyncio.run(_serve(host, port, providers, routing, settings))
    except KeyboardInterrupt:
        raise SystemExit(0)


@cli.command()
@click.argument("profile", required=False)
@click.option("--port", default=20128, show_default=True, help="Listen port.")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
def serve(profile, port: int, host: str):
    """Start the awerouter daemon for PROFILE.

    PROFILE is a profile id from routing.json. If omitted, auto-selects when only
    one profile exists.
    """
    _run_serve(profile, port, host)


@click.command("__serve_profile__", hidden=True)
@click.option("--port", default=20128, show_default=True, help="Listen port.")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.pass_context
def _serve_profile(ctx, port: int, host: str):
    """Bare profile launch: `awerouter <profile>` == `awerouter serve <profile>`."""
    _run_serve(ctx.meta["profile_name"], port, host)


cli.add_command(_serve_profile)


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
    protocol = click.prompt("Protocol", type=click.Choice(PROTOCOL_IDS), default="anthropic")
    known = set(providers_all.get(protocol, {}))

    def ask_tier(tier: str) -> str:
        hint = ", ".join(sorted(known)) or "none yet"
        pname = click.prompt(f"{tier} provider ({hint})")
        if pname not in known:
            base_url = click.prompt(f"  {pname} base_url")
            auth_var = click.prompt(f"  {pname} auth env var name (stored as ${{VAR}})")
            save_provider(protocol, pname, base_url, f"${{{auth_var}}}")
            known.add(pname)
        model = click.prompt(f"{tier} model id")
        return f"{pname},{model}"

    flash = ask_tier("flash")
    pro = ask_tier("pro")
    threshold = click.prompt("longContextThreshold", default=8000, type=int)
    save_profile_entry(name, protocol, threshold, flash, pro)

    # Fail loudly if the wizard wrote something inconsistent.
    validate_profiles(load_providers(), load_routing()[1])
    click.echo(f"Profile '{name}' added: flash={flash}  pro={pro}  L3>{threshold}")
    click.echo(f"Start it with: awerouter {name}")


@cli.command("list")
def list_profiles():
    """List routing profiles (name, protocol, flash, pro, threshold)."""
    providers_all = load_providers()
    _, profiles = load_routing()
    validate_profiles(providers_all, profiles)
    for name, p in profiles.items():
        flash = p.destinations["flash"]
        pro = p.destinations["pro"]
        click.echo(
            f"{name}\t{p.protocol}\t{flash.provider_name}/{flash.model}"
            f"\t{pro.provider_name}/{pro.model}\tL3>{p.long_context_threshold}"
        )


@cli.command()
@click.argument("profile", required=False)
def show(profile):
    """Show PROFILE (or the whole config) with secrets redacted."""
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


@cli.command()
@click.option("--lines", default=20, show_default=True, help="Tail N entries.")
def log(lines: int):
    """Show recent request logs."""
    from awerouter.logging import tail
    entries = tail(lines)
    if not entries:
        click.echo("(no logs yet)")
        return
    for e in entries:
        status_s = str(e.status) if e.status is not None else "-"
        click.echo(
            f"{e.ts}  {e.request_id[:12]:12s}  {e.destination:7s}  "
            f"{e.provider:12s}  {e.model_out:24s}  {e.label:14s}  "
            f"status={status_s:>3}  {e.ms}ms  {e.bytes}B  "
            f"tokens={e.token_count}  in={e.model_in}"
        )


@cli.command()
def stats():
    """Show aggregated routing stats, grouped by profile."""
    from awerouter.logging import stats as _stats
    s = _stats()
    if not s:
        click.echo("(no logs yet)")
        return
    click.echo(f"total_requests : {s['total_requests']}")
    click.echo(f"total_bytes    : {s['total_bytes']}")
    if s["flash_requests"]:
        click.echo(
            f"pro input offloaded to flash: ~{s['flash_tokens']} tokens "
            f"across {s['flash_requests']} requests"
        )
        click.echo("  (message tokens only — system prompt & tools excluded; conservative)")
    for name, p in sorted(s["by_profile"].items()):
        click.echo()
        click.echo(f"profile {name}  ({p['requests']} requests, ~{p['flash_tokens']} flash tokens):")
        click.echo("  by_label:")
        for k, v in sorted(p["by_label"].items()):
            click.echo(f"    {k:16s} {v}")
        click.echo("  by_destination:")
        for k, v in sorted(p["by_destination"].items()):
            click.echo(f"    {k:10s} {v}")
        click.echo("  by_provider:")
        for k, v in sorted(p["by_provider"].items()):
            click.echo(f"    {k:16s} {v}")


@cli.command()
def calibrate():
    """Show L3 token distribution to tune longContextThreshold.

    Only L3 traffic (default/longContext/image labels) is threshold-sensitive;
    L1 (webSearch) and L2 (background/think) route identically regardless.
    """
    from awerouter.logging import token_distribution
    d = token_distribution()
    if not d:
        click.echo("(no L3 traffic yet — run some non-background/think requests first)")
        return
    click.echo(f"L3 message-token distribution ({d['n']} requests):")
    click.echo("  (messages only — system prompt and tools definitions are excluded)")
    click.echo(f"  min: {d['min']:>7}   p50: {d['p50']:>7}   p75: {d['p75']:>7}")
    click.echo(f"  p90: {d['p90']:>7}   p95: {d['p95']:>7}   p99: {d['p99']:>7}   max: {d['max']:>7}")
    click.echo()
    click.echo("if you set longContextThreshold to:")
    for c in d["candidates"]:
        click.echo(f"  {c['threshold']:>7}   → {c['flash_pct']}% flash, {100 - c['flash_pct']}% pro")


def main(argv=None):
    return cli.main(args=argv, prog_name="awerouter")


if __name__ == "__main__":
    raise SystemExit(main())
