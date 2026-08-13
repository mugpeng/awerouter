"""CLI commands: serve / log / stats.

Imports the click group from config.py and extends it.
"""

import asyncio

import click

from awerouter.config import cli as config_cli, load_config
from awerouter.server import _serve

# Attach config sub-group to the main cli group
cli = config_cli


@cli.command()
@click.option("--port", default=20128, show_default=True, help="Listen port.")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
def serve(port: int, host: str):
    """Start the awerouter daemon."""
    providers, routing = load_config()
    try:
        asyncio.run(_serve(host, port, providers, routing))
    except KeyboardInterrupt:
        raise SystemExit(0)


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
            f"{e.ts}  {e.destination:7s}  {e.provider:12s}  "
            f"{e.model_out:24s}  {e.label:14s}  "
            f"status={status_s:>3}  {e.ms}ms  {e.bytes}B  "
            f"tokens={e.token_count}  in={e.model_in}"
        )


@cli.command()
def stats():
    """Show aggregated routing stats."""
    from awerouter.logging import stats as _stats
    s = _stats()
    if not s:
        click.echo("(no logs yet)")
        return
    click.echo(f"total_requests : {s['total_requests']}")
    click.echo(f"total_bytes    : {s['total_bytes']}")
    click.echo()
    click.echo("by_label:")
    for k, v in sorted(s["by_label"].items()):
        click.echo(f"  {k:16s} {v}")
    click.echo()
    click.echo("by_destination:")
    for k, v in sorted(s["by_destination"].items()):
        click.echo(f"  {k:10s} {v}")
    click.echo()
    click.echo("by_provider:")
    for k, v in sorted(s["by_provider"].items()):
        click.echo(f"  {k:16s} {v}")


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
    click.echo(f"L3 token distribution ({d['n']} requests):")
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
