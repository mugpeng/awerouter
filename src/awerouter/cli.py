"""CLI commands: serve / add / list / show / log / stats / calibrate.

Imports the click group from config.py and extends it.
"""

import asyncio

import click

from awerouter.config import (
    SuggestGroup,
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


def _usage_tail(lines: int):
    from awerouter.logging import tail
    entries = tail(lines)
    if not entries:
        click.echo("(no logs yet)")
        return
    for e in entries:
        status_s = str(e.status) if e.status is not None else "-"
        dur_s = f"/{_fmt_ms(e.duration_ms)}" if e.duration_ms else ""
        click.echo(
            f"{e.ts}  {e.request_id[:12]:12s}  {e.destination:7s}  "
            f"{e.provider:12s}  {e.model_out:24s}  {e.label:14s}  "
            f"status={status_s:>3}  {_fmt_ms(e.ms)}{dur_s}  "
            f"tokens={e.token_count}  in={e.model_in}"
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


@cli.group(cls=SuggestGroup, invoke_without_command=True)
@click.option("--since", default=None,
              help="Count entries from this point on: 'today', 'yesterday', Nd (e.g. 7d), or YYYY-MM-DD.")
@click.option("--profile", "profile_name", default=None, help="Count entries for one routing profile only.")
@click.pass_context
def usage(ctx, since, profile_name):
    """Usage analytics over the request log (stats is the default view).

    Window options sit between `usage` and the subcommand, e.g.:

    awerouter usage --since today savings
    """
    ctx.ensure_object(dict)
    ctx.obj["since"] = since
    ctx.obj["profile"] = profile_name
    if ctx.invoked_subcommand is None:
        _usage_stats(since, profile_name)


@usage.command()
@click.option("--lines", default=20, show_default=True, help="Tail N entries.")
def tail(lines: int):
    """Show recent request log entries verbatim."""
    _usage_tail(lines)


@usage.command()
@click.option("--clean", is_flag=True, default=False,
              help="Delete saved request logs (asks for confirmation).")
@click.pass_context
def stats(ctx, clean):
    """Routing summary, grouped by profile."""
    from awerouter.logging import clear_logs
    if clean:
        if click.confirm("Delete all saved request logs (requests.jsonl + rotated backup)?"):
            removed = clear_logs()
            for p in removed:
                click.echo(f"removed {p}")
            if not removed:
                click.echo("(no logs to remove)")
            return
        click.echo("aborted — showing stats instead")
    _usage_stats(ctx.obj["since"], ctx.obj["profile"])


def _usage_stats(since, profile_name):
    from awerouter.logging import stats as collect_stats
    cutoff = _window_cutoff(since, profile_name)
    s = collect_stats(cutoff, profile_name)
    if not s:
        click.echo("(no logs yet)")
        return
    click.echo(f"total_requests : {s['total_requests']}")
    click.echo(f"~total_tokens  : {s['total_tokens']}  (messages only — system prompt & tools excluded)")
    err_pct = round(100 * s["errors"] / s["total_requests"]) if s["total_requests"] else 0
    click.echo(f"errors         : {s['errors']} ({err_pct}%)")
    click.echo(f"fallbacks      : {s['fallbacks']}  (flash failed -> pro)")
    if s["flash_requests"]:
        click.echo(
            f"pro input offloaded to flash: ~{s['flash_tokens']} tokens "
            f"across {s['flash_requests']} requests"
        )
        click.echo("  (message tokens only — system prompt & tools excluded; conservative)")
    for name, p in sorted(s["by_profile"].items()):
        click.echo()
        extras = (f", {p['errors']} error{'s' if p['errors'] != 1 else ''}"
                  f", {p['fallbacks']} fallback{'s' if p['fallbacks'] != 1 else ''}")
        click.echo(f"profile {name}  ({p['requests']} requests, ~{p['flash_tokens']} flash tokens{extras}):")
        lat = p["latency"]
        click.echo("  by_label:")
        _echo_counts(p["by_label"], p["requests"])
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


@usage.command()
@click.pass_context
def calibrate(ctx):
    """Tune longContextThreshold from the L3 token distribution.

    Only L3 traffic (default/longContext/image labels) is threshold-sensitive;
    L1 (webSearch) and L2 (background/think) route identically regardless.
    """
    from awerouter.logging import token_distribution
    cutoff = _window_cutoff(ctx.obj["since"], ctx.obj["profile"])
    d = token_distribution(cutoff, ctx.obj["profile"])
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


# Anthropic-style cache economics for the savings bracket (price multipliers,
# not prices — users apply their own per-token prices).
_CACHE_READ_FACTOR = 0.1
_CACHE_WRITE_FACTOR = 1.25


@usage.command()
@click.pass_context
def savings(ctx):
    """Token accounting vs a pro-only setup (token view, no prices)."""
    _usage_savings(ctx.obj["since"], ctx.obj["profile"])


def _usage_savings(since, profile_name):
    from awerouter.logging import cadence, token_totals
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
    click.echo("message input tokens (input side only — output tokens are not visible to the proxy):")
    click.echo(f"  flash   {flash['tokens']:>9,}   avg {flash['tokens'] // max(flash['requests'], 1):,}/req")
    click.echo(f"  pro     {pro['tokens']:>9,}   avg {pro['tokens'] // max(pro['requests'], 1):,}/req")
    click.echo(f"  total   {total_tok:>9,}")
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
    return cli.main(args=argv, prog_name="awerouter")


if __name__ == "__main__":
    raise SystemExit(main())
