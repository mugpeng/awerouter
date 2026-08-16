"""awerouter — smart LLM router daemon.

Routes Claude Code requests to flash (cheap/fast) or pro (strong/accurate)
providers based on structural request signals. Opaque SSE proxy; no request
body parsing on the response path.
"""

import asyncio
import json
import os
import time
import uuid

import aiohttp
from aiohttp import web

from awerouter.config import expand_value
from awerouter.logging import append, ensure_log_dir
from awerouter.router import resolve
from awerouter.types import RequestLog, ResolveResult


# ---------------------------------------------------------------------------
# Header helpers
# ---------------------------------------------------------------------------

# Headers we always pass through from the client request.
_PASS_THROUGH = frozenset({
    "anthropic-version",
    "content-type",
    "x-api-key",
    "x-request-id",
    "traceparent",
    "tracestate",
})


def _filter_headers(headers: dict) -> dict:
    """Keep only pass-through headers, drop hop-by-hop and auth."""
    out = {}
    for k, v in headers.items():
        if k.lower() in _PASS_THROUGH:
            out[k] = v
    return out


def _set_auth(headers: dict, provider, env: dict | None = None) -> None:
    """Replace any incoming auth header with the destination provider's creds.

    Authorization header auto-prefixes 'Bearer ' if the value lacks it.
    """
    headers.pop("authorization", None)
    headers.pop("x-api-key", None)
    auth_value = expand_value(provider.auth, env)
    if provider.auth_header == "authorization" and not auth_value.lower().startswith("bearer "):
        auth_value = f"Bearer {auth_value}"
    headers[provider.auth_header] = auth_value


# ---------------------------------------------------------------------------
# Upstream proxy (single attempt)
# ---------------------------------------------------------------------------


async def _proxy_request(
    session: aiohttp.ClientSession,
    body: dict,
    dest,
    providers: dict,
    headers: dict,
    path: str,
    timeout: aiohttp.ClientTimeout,
) -> aiohttp.ClientResponse:
    """Fire one upstream request. Raises on network/timeout errors."""
    provider = providers[dest.provider_name]
    upstream_url = provider.base_url.rstrip("/") + path

    # Rewrite model to the destination's real model id (copy: body is reused across retries)
    body = dict(body)
    body["model"] = dest.model

    # Auth
    _set_auth(headers, provider, os.environ)

    return await session.post(
        upstream_url,
        json=body,
        headers=headers,
        timeout=timeout,
        allow_redirects=False,
    )


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------


def _resolve_for_request(body: dict, profile, settings) -> ResolveResult:
    """Shared routing decision for messages and count_tokens."""
    return resolve(
        body.get("model") or None,
        body,
        profile.destinations,
        settings.background_model,
        settings.think_model,
        profile.long_context_threshold,
        settings.web_search_model,
    )


class _RoutingState:
    """Mutable routing state shared across the retry loop."""

    def __init__(self, profile, settings, body: dict):
        self.profile = profile
        self.body = body
        self.inbound_model = body.get("model") or ""
        self.result = _resolve_for_request(body, profile, settings)
        self.attempt = 0
        self.streaming_started = False


def _log_failure(state: _RoutingState, request_id: str, t0: float, status: int) -> None:
    """Log requests that never got an upstream response (502 path)."""
    dest = state.profile.destinations[state.result.destination]
    ensure_log_dir()
    append(RequestLog(
        ts=_now_iso(),
        request_id=request_id,
        model_in=state.inbound_model or "<none>",
        label=state.result.label,
        destination=state.result.destination,
        provider=dest.provider_name,
        model_out=dest.model,
        status=status,
        ms=int((time.monotonic() - t0) * 1000),
        bytes=0,
        token_count=state.result.inspect.token_count,
        profile=state.profile.name,
    ))


async def handle_messages(request: web.Request) -> web.StreamResponse:
    providers: dict = request.app["providers"]
    profile = request.app["profile"]
    settings = request.app["settings"]
    session: aiohttp.ClientSession = request.app["session"]

    t0 = time.monotonic()
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    body = await request.json()
    headers = _filter_headers(dict(request.headers))

    # Timeout: generous for streaming, tight for non-streaming
    is_stream = body.get("stream", False)
    timeout = aiohttp.ClientTimeout(
        connect=10,
        total=None if is_stream else 120,
        sock_read=None if is_stream else 120,
    )

    state = _RoutingState(profile, settings, body)

    while True:
        dest_key = state.result.destination
        dest = state.profile.destinations[dest_key]
        state.attempt += 1

        try:
            up = await _proxy_request(
                session, state.body, dest, providers, dict(headers), request.path, timeout
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            # Network-level failure
            if dest_key == "flash" and state.attempt == 1:
                state.result = _fallback_result(state)
                continue
            _log_failure(state, request_id, t0, 502)
            raise web.HTTPBadGateway(
                text=json.dumps({"error": {"message": f"upstream error: {exc}"}}),
                content_type="application/json",
            )

        # We have a response — decide whether to fallback or stream back
        status = up.status
        is_transient = status in (429, 408) or (status >= 500 and status < 600)

        if is_transient and dest_key == "flash" and state.attempt == 1 and not state.streaming_started:
            up.close()
            state.result = _fallback_result(state)
            continue

        # Success path or non-fallbackable error — stream back
        ms = int((time.monotonic() - t0) * 1000)
        resp = web.StreamResponse(status=status)

        # Copy upstream content-type, anthropic-version
        for h in ("content-type", "anthropic-version", "x-request-id"):
            val = up.headers.get(h)
            if val:
                resp.headers[h] = val

        await resp.prepare(request)

        byte_count = 0
        try:
            async for chunk in up.content.iter_any():
                await resp.write(chunk)
                byte_count += len(chunk)
                state.streaming_started = True
        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError):
            # Client disconnect or upstream mid-stream error — log partial
            status = status if status and status < 400 else (status or 499)
        finally:
            try:
                await resp.write_eof()
            except Exception:
                pass
            up.close()

        # Log (always, even on disconnect — needed for calibration)
        ensure_log_dir()
        append(RequestLog(
            ts=_now_iso(),
            request_id=request_id,
            model_in=state.inbound_model or "<none>",
            label=state.result.label,
            destination=dest_key,
            provider=dest.provider_name,
            model_out=dest.model,
            status=status,
            ms=ms,
            bytes=byte_count,
            token_count=state.result.inspect.token_count,
            profile=profile.name,
        ))

        return resp


def _fallback_result(state: _RoutingState) -> ResolveResult:
    """Return a new resolve result for the pro fallback."""
    pro_dest = state.profile.destinations["pro"]
    return ResolveResult(
        destination="pro",
        model=pro_dest.model,
        label=state.result.label + "→fallback",
        inspect=state.result.inspect,
    )


async def handle_count_tokens(request: web.Request) -> web.Response:
    providers: dict = request.app["providers"]
    profile = request.app["profile"]
    settings = request.app["settings"]
    session: aiohttp.ClientSession = request.app["session"]

    body = await request.json()
    headers = _filter_headers(dict(request.headers))

    # Resolve destination (same logic as messages)
    result = _resolve_for_request(body, profile, settings)
    dest = profile.destinations[result.destination]
    provider = providers[dest.provider_name]

    upstream_url = provider.base_url.rstrip("/") + request.path
    body["model"] = dest.model
    _set_auth(headers, provider, os.environ)

    try:
        async with session.post(
            upstream_url, json=body, headers=headers,
            timeout=aiohttp.ClientTimeout(connect=10, total=30),
        ) as up:
            data = await up.json()
            return web.json_response(data, status=up.status)
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise web.HTTPBadGateway(
            text=json.dumps({"error": {"message": f"upstream error: {exc}"}}),
            content_type="application/json",
        )


async def handle_models(request: web.Request) -> web.Response:
    settings = request.app["settings"]
    models = [
        {"id": settings.background_model, "object": "model"},
        {"id": "auto", "object": "model"},
        {"id": settings.think_model, "object": "model"},
    ]
    return web.json_response({"data": models, "object": "list"})


async def handle_root(request: web.Request) -> web.Response:
    return web.json_response({
        "name": "awerouter",
        "version": request.app["version"],
        "endpoints": [
            "POST /v1/messages",
            "POST /v1/messages/count_tokens",
            "GET /v1/models",
        ],
    })


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _loopback_proxy_warning() -> "str | None":
    """Warn when shell proxy vars would hijack loopback traffic to awerouter.

    Clients honor http_proxy/https_proxy/all_proxy; without 127.0.0.1 in
    no_proxy, requests to awerouter get routed into the proxy — whose own
    127.0.0.1 is itself — so they fail to connect and come back as 502
    with an empty body.
    """
    has_proxy = any(
        os.environ.get(k) for k in
        ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY")
    )
    if not has_proxy:
        return None
    no_proxy = (os.environ.get("no_proxy") or os.environ.get("NO_PROXY") or "").lower()
    if "127.0.0.1" in no_proxy or "localhost" in no_proxy:
        return None
    return (
        "warning: proxy env vars are set, but no_proxy does not exempt loopback\n"
        "  (clients will route awerouter traffic into the proxy and get empty 502s)\n"
        "  fix: export no_proxy=127.0.0.1,localhost NO_PROXY=127.0.0.1,localhost"
    )


def create_app(providers: dict, profile, settings) -> web.Application:
    app = web.Application()
    app["providers"] = providers
    app["profile"] = profile
    app["settings"] = settings
    app["version"] = "0.1.0"

    session = aiohttp.ClientSession()
    app["session"] = session

    app.add_routes([
        web.get("/", handle_root),
        web.get("/v1/models", handle_models),
        web.post("/v1/messages", handle_messages),
        web.post("/v1/messages/count_tokens", handle_count_tokens),
    ])

    async def on_cleanup(app):
        await app["session"].close()

    app.on_cleanup.append(on_cleanup)
    return app


# ---------------------------------------------------------------------------
# Serve command (called from cli.py)
# ---------------------------------------------------------------------------


async def _serve(host: str, port: int, providers: dict, profile, settings) -> None:
    app = create_app(providers, profile, settings)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    print(f"awerouter listening on {host}:{port}  [{profile.name}]")
    print(f"  agent  -> {profile.agent}")
    print(f"  bg     -> {settings.background_model}  think -> {settings.think_model}  main -> auto")
    print(f"  flash  -> {profile.destinations['flash'].provider_name}/{profile.destinations['flash'].model}")
    print(f"  pro    -> {profile.destinations['pro'].provider_name}/{profile.destinations['pro'].model}")
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print()
    print("point Claude Code here:")
    print(f"  export ANTHROPIC_BASE_URL=http://{display_host}:{port}")
    print(f"  tier env: ANTHROPIC_MODEL=auto  "
          f"ANTHROPIC_DEFAULT_HAIKU_MODEL={settings.background_model}  "
          f"ANTHROPIC_DEFAULT_OPUS_MODEL={settings.think_model}")
    warning = _loopback_proxy_warning()
    if warning:
        print()
        print(warning)
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()
