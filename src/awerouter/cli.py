"""CLI commands: serve / add / list / config / usage.

Imports the click group from config.py and extends it.
"""

import asyncio
import shutil

import click

from awerouter.config import (
    DEFAULT_PORT,
    SuggestGroup,
    backup_path,
    cli as config_cli,
    config_dir,
    die,
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
from awerouter.types import AutoThresholdConfig

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


def _run_serve(profile, port, host: str) -> None:
    if profile:
        providers, routing, settings = load_for_profile(profile)
    else:
        providers, routing, settings = load_default_profile()
    port, port_explicit = _resolve_port(port, routing)
    try:
        asyncio.run(_serve(host, port, providers, routing, settings, port_explicit))
    except KeyboardInterrupt:
        raise SystemExit(0)


@cli.command()
@click.argument("profile", required=False)
@click.option("--port", default=None, type=int,
              help="Listen port (overrides the profile's 'port'; default 20128).")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
def serve(profile, port: int, host: str):
    """Start the awerouter daemon for PROFILE.

    PROFILE is a profile id from routing.json. If omitted, auto-selects when only
    one profile exists.
    """
    _run_serve(profile, port, host)


@click.command("__serve_profile__", hidden=True)
@click.option("--port", default=None, type=int,
              help="Listen port (overrides the profile's 'port'; default 20128).")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.pass_context
def _serve_profile(ctx, port: int, host: str):
    """Bare profile launch: `awerouter <profile>` == `awerouter serve <profile>`."""
    _run_serve(ctx.meta["profile_name"], port, host)


cli.add_command(_serve_profile)


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
            auth_var = click.prompt(f"  {pname} auth env var name (stored as ${{VAR}})")
            save_provider(protocol, pname, base_url, f"${{{auth_var}}}")
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


@cli.command("restore")
@click.argument("file", required=False,
                type=click.Choice(["providers", "routing"], case_sensitive=False))
def restore_cmd(file):
    """Restore a config file from its .bak backup (providers | routing).

    Backups are written by `config edit` and the `add` wizard before each write.
    """
    if file is None:
        file = click.prompt("File to restore", type=click.Choice(["providers", "routing"]))
    target = providers_path() if file == "providers" else routing_path()
    bak = backup_path(target)
    if not bak.exists():
        die(f"no backup found: {bak}")
    if not click.confirm(f"Restore {target} from {bak.name}? (overwrites the current file)"):
        click.echo("aborted")
        return
    shutil.copy2(bak, target)
    click.echo(f"restored {target}")
    validate_profiles(load_providers(), load_routing()[1])
    click.echo("config ok")


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
            detail = " ".join(f"{_TOKEN_SHORT.get(k, k)}={v}" for k, v in e.tokens.items()) or "-"
            click.echo(f"{head}tokens={e.token_count}  {detail}")
            continue
        status_s = str(e.status) if e.status is not None else "-"
        dur_s = f"/{_fmt_ms(e.duration_ms)}" if e.duration_ms else ""
        click.echo(
            f"{head}status={status_s:>3}  {_fmt_ms(e.ms)}{dur_s}  "
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
    _usage_log(None if show_all else lines, since, profile_name, tokens_mode)


@usage.command()
@_since_opt
@_profile_opt
def stats(since, profile_name):
    """Routing summary, grouped by profile."""
    _usage_stats(since, profile_name)


_TOKEN_TYPE_ORDER = ("messages", "system", "tools", "tool_results", "tool_calls", "thinking")


@usage.command()
@_since_opt
@_profile_opt
def tokens(since, profile_name):
    """Input-token totals by content type (messages, system, tools, tool I/O)."""
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
    click.echo(f"input tokens by type ({n} requests, all request content):")
    keys = [k for k in _TOKEN_TYPE_ORDER if k in b["by_type"]]
    keys += [k for k in b["by_type"] if k not in _TOKEN_TYPE_ORDER]
    for k in keys:
        v = b["by_type"][k]
        pct = round(100 * v / total) if total else 0
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

    Only L3 traffic (default/longContext/image labels) is threshold-sensitive;
    L1 (webSearch) and L2 (background/think) route identically regardless.
    """
    from awerouter.logging import auto_threshold, token_distribution
    cutoff = _window_cutoff(since, profile_name)
    try:
        settings = load_routing()[0]
    except SystemExit:
        settings = None  # calibrate is a log view; routing.json missing → defaults
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
    _usage_savings(since, profile_name)


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
    click.echo("request input tokens (input side only — output tokens are not visible to the proxy):")
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
