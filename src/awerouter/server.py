"""awerouter — smart LLM router daemon.

Routes coding-agent requests to flash (cheap/fast) or pro (strong/accurate)
providers based on structural request signals. Same-protocol passthrough
proxy (anthropic / openai-chat / openai-responses); no translation, no
request body parsing on the response path.
"""

from __future__ import annotations

import asyncio
import errno
import json
import os
import time
import uuid

import aiohttp
from aiohttp import web

from awerouter import __version__
from awerouter.config import die, expand_value
from awerouter.logging import append, ensure_log_dir
from awerouter.protocols import ENDPOINT_PATHS, extract
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


# Known clients and their User-Agent prefixes, normalized to a stable label.
# awerouter only sees the wire request, so the UA header is the only place
# the caller's identity exists (aweswitch launches clients outside our view).
_AGENT_RULES = (
    ("claude", "claude-code"),
    ("codex", "codex"),
    ("opencode", "opencode"),
    ("cursor", "cursor"),
    ("curl", "curl"),
)


def _agent_from_ua(ua: str) -> str:
    """Best-effort caller identity: 'claude-cli/2.0 (external, cli)' → 'claude-code'.

    Unknown but identifiable clients fall back to the first UA token
    ('python-requests/2.31' → 'python-requests'); empty UA → ''.
    """
    if not ua:
        return ""
    token = ua.split()[0].split("/")[0].lower()
    for prefix, label in _AGENT_RULES:
        if prefix in token:
            return label
    return token


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
# Request handlers
# ---------------------------------------------------------------------------


def _resolve_for_request(body: dict, profile, settings) -> ResolveResult:
    """Shared routing decision for all message-shaped endpoints."""
    feat = extract(profile.protocol, body)
    return resolve(
        body.get("model") or None,
        feat,
        profile.destinations,
        settings.background_model,
        settings.think_model,
        profile.long_context_threshold,
        settings.web_search_model,
        settings.search_result_discount,
    )


class _RoutingState:
    """Mutable routing state shared across the retry loop."""

    def __init__(self, profile, settings, body: dict, agent: str = ""):
        self.profile = profile
        self.body = body
        self.inbound_model = body.get("model") or ""
        self.agent = agent
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
        tokens=state.result.inspect.token_breakdown,
        file_search_tokens=state.result.inspect.file_search_tokens,
        profile=state.profile.name,
        protocol=state.profile.protocol,
        agent=state.agent,
    ))


def _protocol_mismatch(request: web.Request, endpoint_protocol: str) -> web.HTTPBadRequest:
    profile = request.app["profile"]
    return web.HTTPBadRequest(
        text=json.dumps({"error": {"message": (
            f"profile '{profile.name}' speaks '{profile.protocol}'; "
            f"this endpoint serves '{endpoint_protocol}'. "
            "Start a profile of the matching protocol, or point this client elsewhere."
        )}}),
        content_type="application/json",
    )


async def _proxy_flow(request: web.Request, endpoint_protocol: str) -> web.StreamResponse:
    """Generic same-protocol proxy flow: route, forward, retry, stream back, log."""
    providers: dict = request.app["providers"]
    profile = request.app["profile"]
    settings = request.app["settings"]
    session: aiohttp.ClientSession = request.app["session"]

    if profile.protocol != endpoint_protocol:
        raise _protocol_mismatch(request, endpoint_protocol)

    t0 = time.monotonic()
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    body = await request.json()
    headers = _filter_headers(dict(request.headers))
    path = ENDPOINT_PATHS[endpoint_protocol]

    # Timeout: generous for streaming, tight for non-streaming
    is_stream = body.get("stream", False)
    timeout = aiohttp.ClientTimeout(
        connect=10,
        total=None if is_stream else 120,
        sock_read=None if is_stream else 120,
    )

    state = _RoutingState(profile, settings, body, _agent_from_ua(request.headers.get("User-Agent", "")))

    while True:
        dest_key = state.result.destination
        dest = state.profile.destinations[dest_key]
        state.attempt += 1

        try:
            up = await _proxy_request(
                session, state.body, dest, providers, dict(headers), path, timeout
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

        byte_count = 0
        try:
            try:
                await resp.prepare(request)
            except (aiohttp.ClientError, ConnectionError):
                # Client hung up before we wrote headers — not an upstream
                # failure. Log the request as a client disconnect and stop
                # quietly instead of letting aiohttp log a 500 traceback.
                status = 499
            else:
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
        finally:
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
            duration_ms=int((time.monotonic() - t0) * 1000),
            bytes=byte_count,
            token_count=state.result.inspect.token_count,
            tokens=state.result.inspect.token_breakdown,
            file_search_tokens=state.result.inspect.file_search_tokens,
            profile=profile.name,
            protocol=profile.protocol,
            agent=state.agent,
        ))

        return resp


async def handle_messages(request: web.Request) -> web.StreamResponse:
    return await _proxy_flow(request, "anthropic")


async def handle_chat_completions(request: web.Request) -> web.StreamResponse:
    return await _proxy_flow(request, "openai-chat")


async def handle_responses(request: web.Request) -> web.StreamResponse:
    return await _proxy_flow(request, "openai-responses")


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

    if profile.protocol != "anthropic":
        raise _protocol_mismatch(request, "anthropic")

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
            "POST /v1/chat/completions",
            "POST /v1/responses",
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
    app["version"] = __version__

    session = aiohttp.ClientSession()
    app["session"] = session

    app.add_routes([
        web.get("/", handle_root),
        web.get("/v1/models", handle_models),
        web.post("/v1/messages", handle_messages),
        web.post("/v1/messages/count_tokens", handle_count_tokens),
        web.post("/v1/chat/completions", handle_chat_completions),
        web.post("/v1/responses", handle_responses),
        # Unversioned aliases: OpenAI-style clients whose base_url omits /v1
        # (or includes it) both work. Handlers forward the canonical upstream
        # path regardless of the inbound one.
        web.get("/models", handle_models),
        web.post("/chat/completions", handle_chat_completions),
        web.post("/responses", handle_responses),
    ])

    async def on_cleanup(app):
        await app["session"].close()

    app.on_cleanup.append(on_cleanup)
    return app


# ---------------------------------------------------------------------------
# Serve command (called from cli.py)
# ---------------------------------------------------------------------------


def _client_hint(protocol: str, display_host: str, port: int, settings) -> str:
    if protocol == "anthropic":
        return (
            "point Claude Code here:\n"
            f"  export ANTHROPIC_BASE_URL=http://{display_host}:{port}\n"
            f"  tier env: ANTHROPIC_MODEL=auto  "
            f"ANTHROPIC_DEFAULT_HAIKU_MODEL={settings.background_model}  "
            f"ANTHROPIC_DEFAULT_OPUS_MODEL={settings.think_model}"
        )
    wire_api = "chat" if protocol == "openai-chat" else "responses"
    return (
        "point your OpenAI client here:\n"
        f"  export OPENAI_BASE_URL=http://{display_host}:{port}/v1\n"
        f"  (base_url with or without /v1 both work)\n"
        f"  codex: set base_url to the same URL in config.toml (wire_api = \"{wire_api}\")"
    )


# How far past the default port an implicit serve scans before giving up.
_PORT_SCAN_SPAN = 100


async def _serve(host: str, port: int, providers: dict, profile, settings,
                 port_explicit: bool = False) -> None:
    app = create_app(providers, profile, settings)
    runner = web.AppRunner(app)
    await runner.setup()

    async def _bind(p: int) -> web.TCPSite:
        site = web.TCPSite(runner, host=host, port=p)
        await site.start()
        return site

    site = None
    if port_explicit:
        # An explicitly chosen port (--port or the profile's port field) must
        # not silently move: clients hardcode it.
        try:
            site = await _bind(port)
        except OSError:
            await runner.cleanup()
            die(
                f"port {port} is already in use — another awerouter (or process) is holding it.\n"
                f"  stop it first, or launch with a different --port"
            )
    else:
        # Implicit default: take the first free port scanning up from it, so
        # concurrent instances get predictable sequential ports (20128, 20129,
        # ...) in start order instead of random ones.
        for candidate in range(port, port + _PORT_SCAN_SPAN):
            try:
                site = await _bind(candidate)
                if candidate != port:
                    print(f"  note         -> port {port} busy; using next free port {candidate}")
                break
            except OSError as exc:
                if exc.errno != errno.EADDRINUSE:
                    await runner.cleanup()
                    die(f"cannot bind {host}:{candidate}: {exc}")
    if site is None:
        await runner.cleanup()
        die(f"no free port in {port}-{port + _PORT_SCAN_SPAN - 1}; pass --port explicitly")
    actual_port = site._server.sockets[0].getsockname()[1]
    print(f"awerouter listening on {host}:{actual_port}  [{profile.name}]")
    print(f"  protocol      -> {profile.protocol}")
    if profile.port is not None:
        print(f"  port          -> {profile.port} (from routing.json; --port overrides)")
    print(f"  bg            -> {settings.background_model}  "
          f"think -> {settings.think_model}  "
          f"main -> auto  web_search -> {settings.web_search_model}")
    print(f"  flash  -> {profile.destinations['flash'].provider_name}/{profile.destinations['flash'].model}")
    print(f"  pro    -> {profile.destinations['pro'].provider_name}/{profile.destinations['pro'].model}")
    print(f"  L3 threshold -> {profile.long_context_threshold}")
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print()
    print(_client_hint(profile.protocol, display_host, actual_port, settings))
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
