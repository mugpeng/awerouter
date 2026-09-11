"""awerouter — smart LLM router daemon.

Routes coding-agent requests to flash (cheap/fast) or pro (strong/accurate)
providers based on structural request signals. Same-protocol passthrough
proxy (anthropic / openai-chat / openai-responses); no translation, no
request body parsing on the response path. Profiles may opt into rtk
tool-result compression on the request path (default off).
"""

from __future__ import annotations

import asyncio
import errno
import json
import os
import signal
import time
import urllib.request
import uuid
from dataclasses import dataclass

import aiohttp
from aiohttp import web

from awerouter import __version__
from awerouter import odcp, rtk
from awerouter import runtime
from awerouter.claude import (
    AUTH_SENTINEL as CLAUDE_SENTINEL,
    ClaudeAuthError,
    apply_claude_auth,
    claude_auth_path,
    login_status,
)
from awerouter.codex import AUTH_SENTINEL, CodexAuthError, apply_codex_auth, load_codex_login
from awerouter.config import (
    die,
    expand_value,
    is_loopback_url,
    load_for_profile,
    load_providers,
    load_routing,
    providers_path,
    routing_path,
    validate_profiles,
)
from awerouter.logging import append, auto_threshold, ensure_log_dir
from awerouter.protocols import ENDPOINT_PATHS, extract
from awerouter.router import build_direct_queue, build_queue, resolve
from awerouter.types import (
    Destination,
    RequestLog,
    ResolveResult,
    RoutingProfile,
    Settings,
)
from awerouter.update_check import cached_update_hint

# awecompress (frozen-summary history compression) runs in-process beside
# odcp/rtk when a profile opts in. Soft import: the flag dies at serve start
# with an install hint when the package is absent — awerouter works without it.
try:
    from awecompress.integrate import Compressor, Knobs
except ImportError:  # pragma: no cover — only reachable without the package
    Compressor = None
    Knobs = None
from awerouter.vision import (
    CAPTIONS,
    build_caption_body,
    cache_key,
    collect_images,
    image_key,
    parse_caption_response,
    replace_images,
)


# Per-request opt-out for rtk compression and odcp pruning (value "off"
# disables both), so a debugging session can see raw tool output without
# touching routing.json.
TOKEN_SAVER_HEADER = "x-awerouter-token-saver"


def _token_saver_on(request: web.Request) -> bool:
    return request.headers.get(TOKEN_SAVER_HEADER, "").lower() != "off"


def _rtk_enabled(request: web.Request, profile) -> bool:
    return profile.rtk and _token_saver_on(request)


def _odcp_enabled(request: web.Request, profile) -> bool:
    return profile.odcp is not None and _token_saver_on(request)


def _awecompress_enabled(request: web.Request, profile) -> bool:
    return profile.awecompress is not None and _token_saver_on(request)


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
        name = k.lower()
        if name in _PASS_THROUGH:
            out[name] = v
    return out


async def _set_auth(headers: dict, provider, env: dict | None = None,
                    force_claude_refresh: bool = False) -> None:
    """Replace any incoming auth header with the destination provider's creds.

    No-auth providers (local model servers) send no auth header at all — the
    client's incoming key is dropped, not forwarded. Authorization header
    auto-prefixes 'Bearer ' if the value lacks it. The 'codex' sentinel loads
    the local Codex CLI login and writes the ChatGPT account header set; the
    'claude' sentinel loads the awerouter-owned OAuth login (off the event
    loop — a stale token refreshes over the network here).
    """
    headers.pop("authorization", None)
    headers.pop("x-api-key", None)
    if not provider.auth:
        return
    if provider.auth == AUTH_SENTINEL:
        apply_codex_auth(headers, provider.auth_home or None)
        return
    if provider.auth == CLAUDE_SENTINEL:
        await asyncio.to_thread(apply_claude_auth, headers, provider.auth_home or None,
                                force_claude_refresh)
        return
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


def _codex_proxy(base_url: str) -> "str | None":
    """Subscription-login backends (chatgpt.com, api.anthropic.com) commonly
    need the shell proxy to be reachable; honor the same env vars the codex
    and claude CLIs honor (https_proxy/all_proxy, system settings on macOS).
    Loopback targets never take the proxy (local relays); other providers
    stay direct, exactly as before."""
    if is_loopback_url(base_url):
        return None
    proxies = urllib.request.getproxies()
    proxy = proxies.get("https") or proxies.get("all")
    if proxy and not proxy.startswith(("http://", "https://")):
        return None  # socks proxies would need aiohttp-socks; not a dependency
    return proxy


def _codex_sse_response(raw: bytes) -> "dict | None":
    """Extract the final `response` object from a codex SSE stream.

    Returns the object carried by the last response.completed / response.failed
    event, or None when the stream ended without either (truncated / no
    terminal event). The codex backend's terminal object omits the output
    items, so they are rebuilt from the response.output_item.done events.
    """
    found = None
    items = []
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.startswith("data:"):
            continue
        try:
            payload = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        ptype = payload.get("type")
        if ptype == "response.output_item.done" and isinstance(payload.get("item"), dict):
            items.append(payload["item"])
        elif ptype in ("response.completed", "response.failed"):
            found = payload.get("response")
    if isinstance(found, dict) and not found.get("output") and items:
        found["output"] = items
    return found


async def _proxy_request(
    session: aiohttp.ClientSession,
    body: dict,
    dest,
    providers: dict,
    headers: dict,
    path: str,
    timeout: aiohttp.ClientTimeout,
    force_claude_refresh: bool = False,
) -> aiohttp.ClientResponse:
    """Fire one upstream request. Raises on network/timeout errors."""
    provider = providers[dest.provider_name]
    upstream_url = provider.base_url.rstrip("/") + path

    # Rewrite model to the destination's real model id (copy: body is reused across retries)
    body = dict(body)
    body["model"] = dest.model
    if provider.auth == AUTH_SENTINEL:
        # The ChatGPT Codex backend is zero-data-retention: the CLI itself
        # always sends store=false, and a client's store=true is rejected.
        body["store"] = False
        # Sampling controls are CLI-internal; clients that send them get 400s.
        body.pop("max_output_tokens", None)
        if not body.get("stream"):
            # The backend only speaks SSE. A non-streaming client gets the
            # stream buffered back into one JSON response (in _proxy_flow).
            body["stream"] = True

    # Auth
    await _set_auth(headers, provider, os.environ, force_claude_refresh)

    sub_auth = provider.auth in (AUTH_SENTINEL, CLAUDE_SENTINEL)
    return await session.post(
        upstream_url,
        json=body,
        headers=headers,
        timeout=timeout,
        allow_redirects=False,
        proxy=_codex_proxy(provider.base_url) if sub_auth else None,
    )


# ---------------------------------------------------------------------------
# Image bridge (opt-in): flash transcribes history images to text
# ---------------------------------------------------------------------------

CAPTION_TIMEOUT = aiohttp.ClientTimeout(connect=10, total=60)


async def _caption_image(session, provider, model: str, protocol: str,
                         image_part: dict) -> str:
    """One non-streaming transcription call to the multimodal destination.

    Raises on any failure — the caller falls back to the plain image route.
    """
    body = build_caption_body(protocol, model, image_part)
    headers = {"content-type": "application/json"}
    if protocol == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
    await _set_auth(headers, provider, os.environ)
    url = provider.base_url.rstrip("/") + ENDPOINT_PATHS[protocol]
    async with session.post(url, json=body, headers=headers,
                            timeout=CAPTION_TIMEOUT, allow_redirects=False) as up:
        if up.status != 200:
            raise RuntimeError(f"caption upstream status {up.status}")
        data = await up.json(content_type=None)
    caption = parse_caption_response(protocol, data)
    if not caption:
        raise RuntimeError("caption response carried no text")
    return caption


async def _bridge_images(request_id: str, session, body: dict, protocol: str,
                         profile, providers: dict, settings) -> bool:
    """Replace history images with flash transcriptions so a text-only pro
    can continue the session. Returns True when the body was rewritten.

    Fires only when the request carries images but NOT in the final message
    (a fresh upload routes to the multimodal imageModel natively). Every
    caption must succeed before any rewrite happens; on failure the body
    stays untouched and the L1 image guard routes the request as before.
    """
    feat = extract(protocol, body)
    if not (feat.has_image and not feat.has_new_image):
        return False
    dest = profile.destinations[settings.image_model]
    provider = providers[dest.provider_name]
    if provider.auth == AUTH_SENTINEL:
        return False  # SSE-only codex backend cannot serve non-streaming captions

    captions: dict = {}
    for part in collect_images(body, protocol):
        ihash = image_key(protocol, part)
        caption = CAPTIONS.get(cache_key(provider.name, dest.model, ihash))
        if caption is None:
            t0 = time.monotonic()
            try:
                caption = await _caption_image(
                    session, provider, dest.model, protocol, part)
            except Exception as exc:  # any failure falls back to the image route
                print(f"  bridge: caption failed ({exc}); {request_id} "
                      f"keeps the image route -> {settings.image_model}")
                return False
            CAPTIONS.put(cache_key(provider.name, dest.model, ihash), caption)
            print(f"  bridge: {dest.provider_name}/{dest.model} transcribed image "
                  f"{ihash[:8]} in {time.monotonic() - t0:.1f}s")
        captions[ihash] = caption
    replace_images(body, protocol, dest.model, captions)
    return True


# ---------------------------------------------------------------------------
# Request handlers
# ---------------------------------------------------------------------------


def _resolve_for_request(body: dict, profile, settings, protocol: str,
                         resolve_model: "str | None" = None) -> ResolveResult:
    """Shared routing decision for all message-shaped endpoints.

    resolve_model: what the routing pipeline matches — defaults to the body's
    model. Gateway requests pass the tier part of the alias instead (""
    = the 'auto' tier, matching no L2 label, so the full pipeline runs;
    "flash"/"pro" normalize to the profile's own background/think labels)."""
    feat = extract(protocol, body)
    tr = settings.tool_routing
    return resolve(
        resolve_model if resolve_model is not None else (body.get("model") or None),
        feat,
        profile.destinations,
        settings.background_model,
        settings.think_model,
        profile.long_context_threshold,
        tr.web_search or settings.web_search_model,
        settings.search_result_discount,
        tr.edit,
        settings.image_model,
        settings.default_model,
    )


def _hop(state: "_RoutingState", pos: int) -> None:
    """Move the request to queue[pos], stamping the failover into the label
    (so the degradation is visible per request and in the usage log)."""
    cand = state.queue[pos]
    state.queue_pos = pos
    state.result = ResolveResult(
        destination=cand.tier,
        model=cand.dest.model,
        label=state.result.label + f"→fb:{cand.dest.provider_name},{cand.dest.model}",
        inspect=state.result.inspect,
    )
    state.fallback_hops += 1


class _RoutingState:
    """Mutable routing state shared across the retry loop."""

    def __init__(self, profile, settings, body: dict, agent: str = "", rtk_saved: int = 0,
                 odcp_saved: int = 0, awecompress_saved: int = 0, protocol: str = "",
                 resolve_model: "str | None" = None,
                 direct_dest: Destination | None = None, providers: dict | None = None):
        self.profile = profile
        self.body = body
        self.inbound_model = body.get("model") or ""
        self.agent = agent
        self.rtk_saved = rtk_saved
        self.odcp_saved = odcp_saved
        self.awecompress_saved = awecompress_saved
        self.protocol = protocol
        self.direct_dest = direct_dest
        if direct_dest is None:
            self.result = _resolve_for_request(body, profile, settings, protocol, resolve_model)
            # resolve() picks the tier; the queue decides who serves it, in
            # order, when a candidate dies (429/quota/5xx/network, pre-stream).
            self.queue = build_queue(self.result.destination, profile,
                                     self.result.inspect, providers)
        else:
            self.result = ResolveResult(
                destination="direct", model=direct_dest.model, label="direct",
                inspect=extract(protocol, body),
            )
            # Unpooled stays pinned by name (build_direct_queue returns a
            # length-1 queue, so "never falls back" needs no special case in
            # the retry loop); a provider 'pool' tag expands into same-model
            # account failover.
            self.queue = build_direct_queue(direct_dest, self.result.inspect, providers)
        self.queue_pos = 0
        self.fallback_hops = 0
        self.tried: list[str] = []   # candidates attempted, named in the exhausted error
        # Subscription logins that answered 401 twice (dead account token) —
        # no later hop may ride them again.
        self.rejected_auths: set = set()
        if providers is not None:
            # Start at the first candidate awake from a recent quota
            # rejection, so a dead window does not tax every request with a
            # doomed first hit. Advisory: all cooling -> primary as usual.
            for pos, cand in enumerate(self.queue):
                if not _cooling(cand.dest, self.protocol):
                    if pos > 0:
                        _hop(self, pos)
                    break
        self.streaming_started = False
        self.codex_retried = False          # 401 auth retry happened (codex re-read / claude refresh)
        self.claude_force_refresh = False   # next upstream call forces a claude token refresh
        self.codex_stream_fix = False

    @property
    def log_profile(self) -> str:
        """Usage-log attribution. A direct forward belongs to no routing
        profile; logging the context profile (an arbitrary pick among those
        serving the protocol) would misattribute it in `usage --profile`."""
        return "direct" if self.direct_dest is not None else self.profile.name


def _append_log(state: _RoutingState, request_id: str, t0: float,
                status: "int | None", dest, dest_key: str, byte_count: int = 0,
                ms: "int | None" = None, duration_ms: "int | None" = None) -> None:
    """One usage-log row for a finished request attempt — the single place
    the RequestLog field set lives (failed, buffered-codex, and streamed
    attempts all log the same shape). ms defaults to now (time to first
    byte); duration_ms defaults to now too — _log_failure passes 0 because
    no response ever arrived."""
    now_ms = int((time.monotonic() - t0) * 1000)
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
        ms=ms if ms is not None else now_ms,
        duration_ms=duration_ms if duration_ms is not None else now_ms,
        bytes=byte_count,
        token_count=state.result.inspect.token_count,
        tokens=state.result.inspect.token_breakdown,
        file_search_tokens=state.result.inspect.file_search_tokens,
        rtk_saved=state.rtk_saved,
        odcp_saved=state.odcp_saved,
        awecompress_saved=state.awecompress_saved,
        profile=state.log_profile,
        protocol=state.protocol,
        agent=state.agent,
        codex_retried=state.codex_retried,
        fallback_hops=state.fallback_hops,
    ))


def _log_failure(state: _RoutingState, request_id: str, t0: float, status: int) -> None:
    """Log requests that never got an upstream response (502/503 path)."""
    cand = state.queue[state.queue_pos]
    _append_log(state, request_id, t0, status, cand.dest, cand.tier, duration_ms=0)


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


# ---------------------------------------------------------------------------
# Gateway mode: one port, every profile — the model name picks the profile
# ---------------------------------------------------------------------------


@dataclass
class _GatewayEntry:
    """One profile as the gateway serves it: its own effective settings and
    the provider groups of every protocol it speaks."""
    profile: "RoutingProfile"
    settings: "Settings"
    providers: dict  # {protocol: {provider_name: Provider}} for served protocols
    direct_dest: Destination | None = None  # explicit provider/model gateway request


def _gateway_error(message: str) -> web.HTTPBadRequest:
    return web.HTTPBadRequest(
        text=json.dumps({"error": {"message": message}}),
        content_type="application/json",
    )


def _gateway_default_name(app) -> "str | None":
    """The profile bare model names route to: routing.json's defaultProfile,
    or the only profile when there is exactly one."""
    default = app["default_profile"]
    entries: dict = app["gateway"]
    return default or (next(iter(entries)) if len(entries) == 1 else None)


def _gateway_select(app, model: "str | None", endpoint_protocol: str) -> tuple[_GatewayEntry, str]:
    """Resolve a gateway request to (entry, resolve_model) from the model name.

    'profile/tier' picks the profile; the tier normalizes to the profile's own
    L2 labels so forcing works even when they are customized ('haiku' etc.).
    A bare name goes to the default profile and keeps its label meaning.
    Anything unknown raises a 400 that names what to do instead.
    """
    entries: dict = app["gateway"]
    if model and "/" in model:
        name, _, tier = model.partition("/")
        entry = entries.get(name)
        if entry is not None:
            if endpoint_protocol not in entry.profile.protocols:
                serving = sorted(n for n, e in entries.items()
                                 if endpoint_protocol in e.profile.protocols)
                pick = f" Profiles serving it: {', '.join(serving)}." if serving else ""
                raise _gateway_error(
                    f"profile '{name}' speaks '{entry.profile.protocol}'; "
                    f"this endpoint serves '{endpoint_protocol}'.{pick}"
                )
            s = entry.settings
            if tier == "auto":
                resolve_model = ""
            elif tier in ("flash", s.background_model):
                resolve_model = s.background_model
            elif tier in ("pro", s.think_model):
                resolve_model = s.think_model
            else:
                raise _gateway_error(
                    f"model '{model}': tier must be 'auto', 'flash', 'pro', or one of this "
                    f"profile's own labels ('{s.background_model}' / '{s.think_model}')"
                )
            return entry, resolve_model

        # Explicit provider/model names are fixed forwards, not routing tiers.
        provider = next((e.providers.get(endpoint_protocol, {}).get(name)
                         for e in entries.values()
                         if name in e.providers.get(endpoint_protocol, {})), None)
        if provider is not None:
            if tier not in provider.models:
                declared = ", ".join(provider.models) or "(none)"
                raise _gateway_error(
                    f"model '{model}' is not declared by provider '{name}'; "
                    f"declared models: {declared}"
                )
            context = next((e for e in entries.values()
                            if endpoint_protocol in e.profile.protocols), None)
            if context is None:
                raise _gateway_error(
                    f"no profile serves protocol '{endpoint_protocol}' for model '{model}'"
                )
            direct = _GatewayEntry(
                profile=context.profile,
                settings=context.settings,
                providers=context.providers,
                direct_dest=Destination(name, tier),
            )
            return direct, ""

        avail = ", ".join(sorted(entries)) or "(none)"
        raise _gateway_error(
            f"unknown profile '{name}' or provider in model '{model}'; "
            f"available profiles: {avail}. Use '<profile>/auto|flash|pro', or "
            "a declared '<provider>/<model>'; GET /v1/models lists them."
        )
    # Bare name (or none at all): the default profile's tiers, exactly as a
    # single-profile serve would treat them.
    default_name = _gateway_default_name(app)
    entry = entries.get(default_name) if default_name else None
    if entry is None:
        raise _gateway_error(
            f"bare model name {model or '<none>'!r} has no default to route to: set "
            "routing.json 'defaultProfile' or use '<profile>/auto|flash|pro'; "
            f"profiles: {', '.join(sorted(entries)) or '(none)'}"
        )
    if endpoint_protocol not in entry.profile.protocols:
        raise _gateway_error(
            f"the default profile '{entry.profile.name}' speaks "
            f"'{entry.profile.protocol}'; this endpoint serves '{endpoint_protocol}'. "
            "Pick a '<profile>/…' name that serves it, or change defaultProfile."
        )
    return entry, model or ""


# ---------------------------------------------------------------------------
# awecompress (frozen-summary history compression, in-process)
# ---------------------------------------------------------------------------

# Generous: a summary call carries a transcript and waits on a full
# non-streaming completion.
AWECOMPRESS_SUMMARY_TIMEOUT = aiohttp.ClientTimeout(connect=10, total=120)


def _awecompress_validate(profile, providers_all: dict) -> None:
    """Serve-start cross-check: the package must be importable and a literal
    summaryModel must be servable in every protocol group the profile speaks.
    Tier names ("", "flash", "pro") ride the destinations and need no check."""
    if profile.awecompress is None:
        return
    if Compressor is None:
        raise SystemExit(
            f"awerouter: profile '{profile.name}' has 'awecompress' on but the "
            "awecompress package is not installed — run: pip install awecompress")
    sm = profile.awecompress.summary_model
    if sm in ("", "flash", "pro"):
        return
    for protocol in profile.protocols:
        group = providers_all.get(protocol) or {}
        for provider in group.values():
            if sm in provider.models:
                break
        else:
            declared = sorted({m for p in group.values() for m in p.models})
            raise SystemExit(
                f"awerouter: profile '{profile.name}' awecompress summaryModel "
                f"'{sm}' is not declared by any {protocol} provider; declared: "
                f"{', '.join(declared) or '(none)'}")


def _awecompress_compressor(app) -> "Compressor":
    """One Compressor per app, sharing the standalone proxy's default store
    (~/.config/awecompress/summaries.db — session keys are hashes, so both
    hosts' sessions coexist; `awecompress status/clear` manage it)."""
    comp = app.get("awecompress")
    if comp is None:
        from awecompress.config import db_path
        comp = Compressor(str(db_path()))
        app["awecompress"] = comp
    return comp


def _awecompress_summary_destination(profile, providers: dict):
    """Who serves summary calls: the flash destination by default, pro on
    request, or the provider declaring a literal model id."""
    sm = profile.awecompress.summary_model
    if sm == "pro":
        return profile.destinations["pro"]
    if sm not in ("", "flash"):
        for pname, provider in providers.items():
            if sm in provider.models:
                return Destination(pname, sm)
        # Unreachable after serve-start validation; a hot-reload that broke
        # the declaration falls back to the safe default rather than dying
        # mid-request (fail-open, like the rest of this path).
        print(f"[awecompress] summaryModel '{sm}' no longer declared; "
              f"summaries fall back to the flash destination")
    return profile.destinations["flash"]


async def _awecompress_apply(app, session, body: dict, protocol: str,
                             profile, providers: dict,
                             allow_summary: bool = True) -> int:
    """One Compressor.transform() call in the request path. Mutates the body
    in place on compression; returns the estimated saved tokens (0 = nothing
    done). Fail-open: any error leaves the body as-is."""
    try:
        comp = _awecompress_compressor(app)
        c = profile.awecompress
        knobs = Knobs(
            threshold_tokens=c.threshold_tokens,
            keep_recent_turns=c.keep_recent_turns,
            min_span_tokens=c.min_span_tokens,
            transcript_result_cap=c.transcript_result_cap,
            protected_tools=c.protected_tools,
            protected_file_patterns=c.protected_file_patterns,
        )
        dest = model = None

        async def sender(request_body: dict) -> dict:
            # _proxy_request handles the provider's auth (subscription logins
            # included), the model rewrite, codex quirks, and the shell proxy.
            headers = {"content-type": "application/json"}
            if protocol == "anthropic":
                headers["anthropic-version"] = "2023-06-01"
            up = await _proxy_request(session, request_body, dest, providers,
                                      headers,
                                      ENDPOINT_PATHS[protocol],
                                      AWECOMPRESS_SUMMARY_TIMEOUT)
            try:
                if up.status != 200:
                    detail = (await up.text())[:200]
                    raise RuntimeError(f"summary upstream {up.status}: {detail}")
                if (up.headers.get("content-type") or "").startswith("text/event-stream"):
                    raw = await up.read()  # codex backend: SSE even when asked not to
                    obj = _codex_sse_response(raw)
                    if obj is None:
                        raise RuntimeError("summary stream ended without a completed response")
                    return obj
                return await up.json(content_type=None)
            finally:
                up.close()

        if allow_summary:
            dest = _awecompress_summary_destination(profile, providers)
            model = dest.model
        outcome = await comp.transform(body, protocol, model, sender if allow_summary else None, knobs)
        if outcome is None:
            return 0
        if allow_summary:
            print(outcome.line)
        return outcome.saved_tokens
    except Exception as exc:  # noqa: BLE001 — fail-open is the contract
        print(f"[awecompress] apply error: {exc}")
        return 0


async def _prepare_body(request: web.Request, request_id: str, session,
                        body: dict, protocol: str, profile, providers: dict,
                        settings, direct_dest=None, allow_summary: bool = True):
    """The body-preparation chain every message-shaped endpoint runs before
    routing — one place, one order: image bridge, awecompress, odcp, rtk.

    Bridge first: history images become flash transcriptions, so what the
    savers compress and the router scores is exactly what goes upstream.
    awecompress before odcp/rtk: the oldest turns become one frozen summary
    first; odcp prunes what remains and rtk shrinks it. All of it runs before
    routing, so L3 decisions, effective_tokens, count_tokens estimates, and
    the usage log reflect what is actually sent (and billed) upstream.

    allow_summary=False (count_tokens) applies existing frozen summaries but
    never mints one — no surprise LLM calls off a token count. Direct gateway
    forwards skip the chain. Returns (awecompress, odcp, rtk) saved-token
    estimates for the usage log; the per-request x-awerouter-token-saver
    header gates the three savers (the bridge is a capability need, not a
    token saver, and always runs when the profile opts in).
    """
    if direct_dest is not None:
        return 0, 0, 0
    if settings.image_bridge:
        await _bridge_images(request_id, session, body, protocol,
                             profile, providers, settings)
    awecompress_saved = odcp_saved = rtk_saved = 0
    if _awecompress_enabled(request, profile):
        awecompress_saved = await _awecompress_apply(
            request.app, session, body, protocol, profile, providers,
            allow_summary=allow_summary)
    if _odcp_enabled(request, profile):
        stats = odcp.prune_body(body, protocol, profile.odcp)
        line = odcp.format_log(stats)
        if line:
            print(line)
        odcp_saved = stats.saved_tokens if stats else 0
    if _rtk_enabled(request, profile):
        stats = rtk.compress_body(body, protocol)
        line = rtk.format_log(stats)
        if line:
            print(line)
        rtk_saved = stats.saved_tokens if stats else 0
    return awecompress_saved, odcp_saved, rtk_saved


async def _proxy_flow(request: web.Request, endpoint_protocol: str) -> web.StreamResponse:
    """Generic same-protocol proxy flow: route, forward, retry, stream back, log."""
    session: aiohttp.ClientSession = request.app["session"]
    gateway = request.app.get("gateway")  # set = gateway mode (profile picked per request)

    t0 = time.monotonic()
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    body = await request.json()
    headers = _filter_headers(dict(request.headers))
    path = ENDPOINT_PATHS[endpoint_protocol]

    if gateway is not None:
        # The model name picks the profile (see _gateway_select); everything
        # below is profile-agnostic from here on.
        entry, resolve_model = _gateway_select(request.app, body.get("model"), endpoint_protocol)
        profile, settings = entry.profile, entry.settings
        providers: dict = entry.providers[endpoint_protocol]
    else:
        profile = request.app["profile"]
        settings = request.app["settings"]
        if endpoint_protocol not in profile.protocols:
            raise _protocol_mismatch(request, endpoint_protocol)
        providers = request.app["providers"][endpoint_protocol]
        resolve_model = None

    # Timeout: generous for streaming, tight for non-streaming
    is_stream = body.get("stream", False)
    timeout = aiohttp.ClientTimeout(
        connect=10,
        total=None if is_stream else 120,
        sock_read=None if is_stream else 120,
    )

    direct_dest = entry.direct_dest if gateway is not None else None
    # One chain, one order, shared with count_tokens (see _prepare_body).
    # Runs once — retries and the flash→pro fallback reuse the same body.
    awecompress_saved, odcp_saved, rtk_saved = await _prepare_body(
        request, request_id, session, body, endpoint_protocol,
        profile, providers, settings, direct_dest)

    state = _RoutingState(profile, settings, body,
                          _agent_from_ua(request.headers.get("User-Agent", "")),
                          rtk_saved, odcp_saved, awecompress_saved, endpoint_protocol, resolve_model,
                          direct_dest, providers)

    while True:
        cand = state.queue[state.queue_pos]
        dest, dest_key = cand.dest, cand.tier
        tag = f"{dest.provider_name},{dest.model}"
        if not state.tried or state.tried[-1] != tag:  # a 401 login retry is the same candidate
            state.tried.append(tag)
        state.codex_stream_fix = (
            providers[dest.provider_name].auth == AUTH_SENTINEL
            and not state.body.get("stream")
        )

        try:
            up = await _proxy_request(
                session, state.body, dest, providers, dict(headers), path, timeout,
                state.claude_force_refresh,
            )
        except (CodexAuthError, ClaudeAuthError) as exc:
            # Login missing/invalid — retrying can't help; tell the user.
            _log_failure(state, request_id, t0, 503)
            raise web.HTTPServiceUnavailable(
                text=json.dumps({"error": {"message": str(exc)}}),
                content_type="application/json",
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            # Network-level failure — next queue candidate, or the error with
            # every attempted candidate named.
            if _next_fallback(state, providers):
                nxt = state.queue[state.queue_pos].dest
                print(f"  {tag} network error ({exc}); {request_id} fails over to "
                      f"{nxt.provider_name},{nxt.model}")
                continue
            _log_failure(state, request_id, t0, 502)
            raise web.HTTPBadGateway(
                text=json.dumps({"error": {"message": (
                    f"upstream error: {exc}; tried {' -> '.join(state.tried)}"
                )}}),
                content_type="application/json",
            )

        # We have a response — decide whether to fail over or stream it back.
        # First streamed byte is the point of no return (SSE cannot be undone).
        status = up.status
        is_transient = status in (429, 408) or (status >= 500 and status < 600)

        if is_transient and not state.streaming_started:
            # Quota/transient rejection: remember it (cooldown), then try the
            # next candidate. An exhausted queue streams this response back
            # untouched — the last upstream error is the honest answer.
            _cooldown_mark(dest, up, status, endpoint_protocol)
            if _next_fallback(state, providers):
                up.close()
                nxt = state.queue[state.queue_pos].dest
                print(f"  {tag} {status}; {request_id} fails over to "
                      f"{nxt.provider_name},{nxt.model}")
                continue

        # A codex-account 401 usually means the login file changed under us
        # (the local CLI refreshed it): re-read auth.json and retry the same
        # destination once before surfacing the 401 to the client. A
        # claude-account 401 gets the same one-shot retry with a forced token
        # refresh (stale clock, token rotated by another process).
        provider = providers[dest.provider_name]
        if (status == 401 and not state.codex_retried
                and provider.auth in (AUTH_SENTINEL, CLAUDE_SENTINEL)):
            up.close()
            state.codex_retried = True
            state.claude_force_refresh = provider.auth == CLAUDE_SENTINEL
            continue

        # Second 401: the login itself is rejected (dead account token, not a
        # mid-flight refresh). Only a candidate riding different credentials
        # can save the request — the rescue remembers the rejected login for
        # every later hop (hard, unlike cooldown; keyed on sentinel+authHome,
        # so one dead account never condemns its healthy sibling) — and prints
        # one line per failover, so a dead login is loud instead of silently
        # burning another destination.
        if status == 401 and provider.auth in (AUTH_SENTINEL, CLAUDE_SENTINEL):
            state.rejected_auths.add(provider.auth_key)
            if _next_fallback(state, providers):
                up.close()
                nxt = state.queue[state.queue_pos].dest
                print(f"  {provider.auth_key} 401 -> login rejected after retry; "
                      f"{request_id} fails over to {nxt.provider_name},{nxt.model}")
                continue

        # A codex 200 for a non-streaming client: the upstream ran SSE (the
        # backend has no non-streaming mode) — buffer it back into one JSON
        # response object, which is what the client asked for.
        if (state.codex_stream_fix and status == 200):
            raw = await up.read()
            up.close()
            byte_count = len(raw)
            obj = _codex_sse_response(raw)
            if obj is None:
                response_body = {
                    "error": {
                        "message": "codex upstream stream ended without a completed response",
                    },
                }
                response_status = 502
            elif obj.get("error"):
                response_body = obj
                response_status = 500
            else:
                response_body = obj
                response_status = 200
            _append_log(state, request_id, t0, response_status, dest, dest_key, byte_count)
            return web.json_response(response_body, status=response_status)

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
        _append_log(state, request_id, t0, status, dest, dest_key, byte_count, ms=ms)

        return resp


async def handle_messages(request: web.Request) -> web.StreamResponse:
    return await _proxy_flow(request, "anthropic")


async def handle_chat_completions(request: web.Request) -> web.StreamResponse:
    return await _proxy_flow(request, "openai-chat")


async def handle_responses(request: web.Request) -> web.StreamResponse:
    return await _proxy_flow(request, "openai-responses")


# Failover cooldown: in-process memory of recently quota-rejected candidates,
# (protocol, provider, model) -> monotonic deadline. The protocol scopes the
# memory: a gateway may serve the same provider name in several protocol
# groups with different accounts — a 429 on one must not sideline the others.
# Advisory, never a hard wall — if every remaining candidate cools down, the
# next one is tried anyway, because a cheap probe beats erroring. Restarts
# clear it; that IS the recovery path.
_COOLDOWN_UNTIL: dict[tuple[str, str, str], float] = {}
_COOLDOWN_DEFAULT_S = 30   # a 429 without Retry-After: probe again after this
_COOLDOWN_MAX_S = 60       # cap however long Retry-After asks for


def _cooldown_mark(dest, up, status: int, protocol: str) -> None:
    """Remember a quota-shaped rejection so upcoming requests skip this
    candidate. Retry-After wins when present and parseable (HTTP-date forms
    are not parsed — the 429 default applies); without it only a 429 counts:
    a bare 5xx is usually a one-off blip, not a signal worth sidelining."""
    secs = None
    ra = up.headers.get("retry-after")
    if ra is not None:
        try:
            secs = int(ra.strip())
        except ValueError:
            secs = None
    if secs is None and status == 429:
        secs = _COOLDOWN_DEFAULT_S
    if not secs or secs < 1:
        return
    _COOLDOWN_UNTIL[(protocol, dest.provider_name, dest.model)] = (
        time.monotonic() + min(secs, _COOLDOWN_MAX_S))


def _cooling(dest, protocol: str) -> bool:
    """True while a recent quota rejection's cooldown still holds."""
    return time.monotonic() < _COOLDOWN_UNTIL.get(
        (protocol, dest.provider_name, dest.model), 0.0)


def _next_fallback(state: _RoutingState, providers: dict) -> bool:
    """Advance the failover queue to the next usable candidate; False when
    exhausted (the caller then surfaces the current response or error).

    Cooldown skips are advisory — all cooling means take the next one anyway;
    state.rejected_auths is a hard filter — a login that already answered
    401 twice (dead account token) must not be ridden again by a later hop,
    it would only re-fail. Each hop is stamped into the label, so the
    degradation is visible per request.
    """
    pick = None
    for pos in range(state.queue_pos + 1, len(state.queue)):
        cand = state.queue[pos]
        if providers[cand.dest.provider_name].auth_key in state.rejected_auths:
            continue
        if pick is None:
            pick = pos  # first candidate the auth filter allows (advisory floor)
        if not _cooling(cand.dest, state.protocol):
            pick = pos  # first awake candidate — the real choice
            break
    if pick is None:
        return False
    _hop(state, pick)
    return True


async def handle_count_tokens(request: web.Request) -> web.Response:
    session: aiohttp.ClientSession = request.app["session"]
    gateway = request.app.get("gateway")

    body = await request.json()
    headers = _filter_headers(dict(request.headers))

    if gateway is not None:
        entry, resolve_model = _gateway_select(request.app, body.get("model"), "anthropic")
        profile, settings = entry.profile, entry.settings
        providers: dict = entry.providers["anthropic"]
        direct_dest = entry.direct_dest
    else:
        profile = request.app["profile"]
        settings = request.app["settings"]
        if "anthropic" not in profile.protocols:
            raise _protocol_mismatch(request, "anthropic")
        providers = request.app["providers"]["anthropic"]
        resolve_model = None
        direct_dest = None

    # Same chain and order as /v1/messages (see _prepare_body): the client's
    # context-window estimate must match what actually gets sent upstream —
    # but never mint a new frozen summary off a token count.
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    await _prepare_body(request, rid, session, body, "anthropic",
                        profile, providers, settings, direct_dest,
                        allow_summary=False)

    # Resolve destination (same logic as messages)
    result = (ResolveResult("direct", direct_dest.model, "direct", extract("anthropic", body))
              if direct_dest is not None
              else _resolve_for_request(body, profile, settings, "anthropic", resolve_model))
    dest = direct_dest or profile.destinations[result.destination]
    provider = providers[dest.provider_name]

    upstream_url = provider.base_url.rstrip("/") + request.path
    body["model"] = dest.model
    await _set_auth(headers, provider, os.environ)

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
    gateway = request.app.get("gateway")
    if gateway is None:
        settings = request.app["settings"]
        ids = [settings.background_model, "auto", settings.think_model]
    else:
        # Gateway: the model name IS the routing key — bare tiers of the
        # default profile (when one exists) plus every profile's three tiers.
        ids = []
        default_name = _gateway_default_name(request.app)
        if default_name:
            s = gateway[default_name].settings
            ids += [s.background_model, "auto", s.think_model]
        for name in sorted(gateway):
            ids += [f"{name}/auto", f"{name}/flash", f"{name}/pro"]
        for entry in gateway.values():
            for group in entry.providers.values():
                for provider_name, provider in group.items():
                    for model in provider.models:
                        model_id = f"{provider_name}/{model}"
                        if model_id not in ids:
                            ids.append(model_id)
    models = [{"id": i, "object": "model"} for i in ids]
    return web.json_response({"data": models, "object": "list"})


async def handle_root(request: web.Request) -> web.Response:
    info = {
        "name": "awerouter",
        "version": request.app["version"],
        "endpoints": [
            "POST /v1/messages",
            "POST /v1/messages/count_tokens",
            "POST /v1/chat/completions",
            "POST /v1/responses",
            "GET /v1/models",
        ],
    }
    if request.app.get("gateway") is not None:
        info["mode"] = "gateway"
        info["models"] = sorted(request.app["gateway"])
    return web.json_response(info)


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


def _flat_providers(providers_by_protocol: dict) -> dict:
    """Flatten grouped providers for the serve-start warnings: one entry per
    provider name (first group wins on name collisions — the warning names
    providers, config show shows the per-group detail)."""
    return {p.name: p for group in providers_by_protocol.values() for p in group.values()}


@web.middleware
async def _daemon_guard(request: web.Request, handler) -> web.StreamResponse:
    """Keep the daemon alive when a request path calls die() (SystemExit) —
    e.g. a ${VAR} auth reference whose value is missing from the daemon's
    environment. One failing request must not take the listener down with it
    (under KeepAlive a request-triggered die() becomes a crash loop): answer
    503 with the message; the next request is unaffected."""
    try:
        return await handler(request)
    except SystemExit as exc:
        message = str(exc).removeprefix("awerouter: ").strip()
        if not message or message.isdigit():
            message = "request failed (awerouter internal error)"
        print(f"daemon kept alive ({request.path}): {message}", flush=True)
        return web.json_response(
            {"error": {"type": "awerouter_error", "message": message}},
            status=503,
        )


def create_app(providers: dict, profile, settings) -> web.Application:
    """providers is the profile's groups keyed by served protocol
    ({protocol: {provider_name: Provider}}); each handler picks its own group."""
    _awecompress_validate(profile, providers)
    app = web.Application(middlewares=[_daemon_guard])
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
        compressor = app.get("awecompress")
        if compressor is not None:
            compressor.close()

    app.on_cleanup.append(on_cleanup)
    return app


def create_gateway_app(entries: dict[str, _GatewayEntry],
                       default_profile: "str | None",
                       providers_all: dict | None = None) -> web.Application:
    """Gateway app: every profile on one port. The request's model name picks
    the profile ('<profile>/auto|flash|pro'); bare names go to default_profile
    (see _gateway_select). Routes are identical to the single-profile app."""
    app = web.Application(middlewares=[_daemon_guard])
    app["gateway"] = entries
    app["default_profile"] = default_profile
    if providers_all:
        for protocol, group in providers_all.items():
            for entry in entries.values():
                if protocol in entry.profile.protocols:
                    entry.providers[protocol] = group
    for entry in entries.values():
        _awecompress_validate(entry.profile, entry.providers)
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
        web.get("/models", handle_models),
        web.post("/chat/completions", handle_chat_completions),
        web.post("/responses", handle_responses),
    ])

    async def on_cleanup(app):
        await app["session"].close()
        compressor = app.get("awecompress")
        if compressor is not None:
            compressor.close()

    app.on_cleanup.append(on_cleanup)
    return app


# ---------------------------------------------------------------------------
# Hot reload: routing.json / providers.json changes apply without a restart
# ---------------------------------------------------------------------------

# How often the watcher polls the config files' mtimes.
_RELOAD_POLL_S = 1.0


def _config_mtimes() -> tuple:
    mtimes = []
    for p in (routing_path(), providers_path()):
        try:
            mtimes.append(p.stat().st_mtime_ns)
        except OSError:  # missing (deleted mid-edit) counts as a change to None
            mtimes.append(None)
    return tuple(mtimes)


def _reload_config(app, profile_name: str) -> bool:
    """Swap a live app's profile/settings/providers for a freshly loaded copy.

    Prints why on refusal and returns False — the running config stays in
    effect until a loadable file shows up. In-flight requests keep whatever
    they read at their start; the swap only affects requests that begin
    after it.
    """
    try:
        new_providers, new_profile, new_settings = load_for_profile(profile_name)
        _awecompress_validate(new_profile, new_providers)  # SystemExit → reload skipped below
    except SystemExit as exc:
        print(f"  config reload skipped (serving the previous config): {exc}")
        return False
    old_profile = app["profile"]
    auto_line = _resolve_auto_threshold(new_profile, new_settings)
    app["providers"] = new_providers
    app["profile"] = new_profile
    app["settings"] = new_settings
    if new_profile.port != old_profile.port:
        print(f"  note -> 'port' for this profile is now {new_profile.port or '(default)'} "
              "in routing.json; restart serve to rebind")
    flash, pro = new_profile.destinations["flash"], new_profile.destinations["pro"]
    print(f"  config reloaded -> flash={flash.provider_name}/{flash.model}  "
          f"pro={pro.provider_name}/{pro.model}  "
          f"L3>{new_profile.long_context_threshold:,}")
    if auto_line is not None:
        print(auto_line)
    return True


async def _watch_config(app, profile_name: "str | None") -> None:
    """Poll config mtimes and reload on change. profile_name None = gateway
    mode: every profile and the bare-name default reload.

    A failed reload announces itself once per file state (mid-save partial
    write, broken JSON) and retries when the file changes again.
    """
    last = _config_mtimes()
    while True:
        await asyncio.sleep(_RELOAD_POLL_S)
        now = _config_mtimes()
        if now == last:
            continue
        if profile_name is not None:
            _reload_config(app, profile_name)
        else:
            _reload_gateway(app)
        last = now


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
    base = (
        "point your OpenAI client here:\n"
        f"  export OPENAI_BASE_URL=http://{display_host}:{port}/v1\n"
        f"  (base_url with or without /v1 both work)\n"
    )
    if protocol == "openai-responses":
        return base + (
            '  codex: set base_url to the same URL in config.toml '
            '(wire_api = "responses")'
        )
    # openai-chat serves non-codex OpenAI-compatible agents (opencode etc.);
    # codex itself needs an openai-responses profile — documented in README.
    return base


def _client_hints(protocols, display_host: str, port: int, settings) -> str:
    """One hint block per served protocol: a multi-protocol profile serves
    every client style on the same port."""
    return "\n\n".join(_client_hint(p, display_host, port, settings) for p in protocols)


# How far past the default port an implicit serve scans before giving up.
_PORT_SCAN_SPAN = 100


# Well-known instance name used by `awerouter serve all` when registering with
# the runtime: it is what `serve status` and `serve stop gateway` match on, and
# it is also the suffix of the background-serve log file. Keeping the literal in
# one place avoids a real profile ever colliding with the gateway on purpose.
GATEWAY_PROFILE_NAME = "gateway"


def _fmt_setting_value(value) -> str:
    """One overrides-line item: strings bare, nested blocks as compact JSON."""
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(", ", ": "))


def _overrides_line(overrides: dict) -> str:
    return "  ".join(f"{k}={_fmt_setting_value(v)}" for k, v in overrides.items())


def _resolve_auto_threshold(profile, settings) -> "str | None":
    """Materialize longContextThreshold: "auto" from this profile's own log.

    Runs once at serve start (before the socket opens, so no request can race
    it); the value stays fixed for the process lifetime. With too few samples
    the fallbackThreshold loaded by config.py stays in effect. Returns the
    banner line to print — the choice must be visible.
    """
    if not profile.threshold_auto:
        return None
    cfg = settings.long_context_auto
    picked = auto_threshold(profile.name, settings.search_result_discount, cfg)
    if picked is not None:
        threshold, samples = picked
        profile.long_context_threshold = threshold
        return (f"  L3 threshold -> auto: p{cfg.percentile} of {samples} L3 requests "
                f"(last {cfg.window_days}d) = {threshold:,}")
    return (f"  L3 threshold -> auto: fewer than {cfg.min_samples} L3 requests in "
            f"last {cfg.window_days}d — fallbackThreshold {cfg.fallback_threshold:,} in effect")


def _noauth_warning(providers: dict) -> "str | None":
    """Warn on no-auth providers pointing off-machine — almost always a
    forgotten 'auth' entry (LAN servers with no auth are the legit exception)."""
    offenders = sorted(
        p.name for p in providers.values()
        if not p.auth and not is_loopback_url(p.base_url)
    )
    if not offenders:
        return None
    return (
        "warning: no auth set for off-machine providers: " + ", ".join(offenders) + "\n"
        "  (cloud APIs need an 'auth' entry; ignore if these are unauthenticated internal servers)"
    )


def _pool_models_warning(groups: dict) -> "str | None":
    """Warn when a pool's members declare different model sets — legal
    (accounts can differ in what they unlocked) but easy to get wrong by
    hand: the direct-forward failover queue for a model not every member
    declares is silently shorter."""
    lines: list[str] = []
    for protocol, group in groups.items():
        pools: dict = {}
        for p in group.values():
            if p.pool:
                pools.setdefault(p.pool, []).append(p)
        for pool, members in pools.items():
            if len({m.models for m in members}) == 1:
                continue
            lines.append(f"warning: pool '{pool}' ({protocol}) members declare "
                         "different models:")
            for m in members:
                models = ", ".join(m.models) or "(none)"
                lines.append(f"  {m.name}: {models}")
            lines.append("  (legal, but failover queues are shorter for models "
                         "not every member declares)")
    return "\n".join(lines) if lines else None


def _by_auth_home(providers: dict, sentinel: str) -> dict:
    """Sentinel providers grouped by their login dir: {authHome: [names]}.
    Providers sharing a login (same authHome) share a verdict; separate
    accounts on one sentinel get one each."""
    grouped: dict[str, list[str]] = {}
    for p in providers.values():
        if p.auth == sentinel:
            grouped.setdefault(p.auth_home, []).append(p.name)
    return grouped


def _codex_login_warning(providers: dict) -> "str | None":
    """Warn when configured Codex providers cannot load their login."""
    lines = []
    for home, names in sorted(_by_auth_home(providers, AUTH_SENTINEL).items()):
        try:
            load_codex_login(home or None)
        except CodexAuthError as exc:
            lines.append(
                "warning: invalid codex login for providers: " + ", ".join(sorted(names)) + "\n"
                f"  ({exc})"
            )
    return "\n".join(lines) if lines else None


def _claude_login_warning(providers: dict) -> "str | None":
    """Claude-login providers with no usable stored login — every request to
    them 503s with an 'awerouter config login claude' hint until the login
    exists. Store check only: a present-but-stale token is fine (it refreshes
    on the first request), so this never touches the network."""
    lines = []
    for home, names in sorted(_by_auth_home(providers, CLAUDE_SENTINEL).items()):
        login_home = home or None
        hint = f"awerouter config login claude{f' {home}' if home else ''}"
        path = claude_auth_path(login_home)
        if path.exists():
            if login_status(login_home) is not None:
                continue
            lines.append(
                "warning: invalid claude login for providers: " + ", ".join(sorted(names)) + "\n"
                f"  ({path} — re-run: {hint})"
            )
        else:
            lines.append(
                "warning: no claude login for providers: " + ", ".join(sorted(names)) + "\n"
                f"  ({path} — run: {hint})"
            )
    return "\n".join(lines) if lines else None


async def _bind_site(runner, host: str, port: int, port_explicit: bool) -> int:
    """Bind the runner's socket and return the actual port. An explicitly
    chosen port (--port or the profile's port field) must not silently move:
    clients hardcode it. The implicit default takes the first free port
    scanning up from it, so concurrent instances get predictable sequential
    ports (20128, 20129, ...) in start order instead of random ones."""
    async def _bind(p: int) -> web.TCPSite:
        site = web.TCPSite(runner, host=host, port=p)
        await site.start()
        return site

    site = None
    if port_explicit:
        try:
            site = await _bind(port)
        except OSError:
            await runner.cleanup()
            die(
                f"port {port} is already in use — another awerouter (or process) is holding it.\n"
                f"  stop it first, or launch with a different --port"
            )
    else:
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
    return site._server.sockets[0].getsockname()[1]


async def _run_until_stopped(runner, watcher) -> None:
    """Common serve tail: wait for SIGTERM/SIGHUP, then tear everything down."""
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGTERM", "SIGHUP"):  # graceful stop / lost terminal
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):  # Windows loops, non-main thread
            pass
    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass
        runtime.unregister()
        await runner.cleanup()


def _serve_warnings(providers: dict, groups: dict) -> None:
    """Serve-start warnings shared by both modes (loopback proxy, no-auth,
    dead logins, pool model drift) over one flat {name: Provider} view, plus
    the per-protocol groups the pool check needs."""
    update_hint = cached_update_hint()
    if update_hint:
        print()
        print(update_hint)
    for warning in (_loopback_proxy_warning(), _noauth_warning(providers),
                    _codex_login_warning(providers), _claude_login_warning(providers),
                    _pool_models_warning(groups)):
        if warning:
            print()
            print(warning)


def _failover_chain(profile, tier: str) -> str:
    """The banner's view of one tier's failover chain: declared backups in
    order, or the implicit cross-tier hop (marked as such)."""
    hops = [f"{d.provider_name}/{d.model}" for d in profile.backups.get(tier, [])]
    if not hops:
        other = "pro" if tier == "flash" else "flash"
        hops = [f"{other} (implicit)"]
    return " -> ".join(hops)


async def _serve(host: str, port: int, providers: dict, profile, settings,
                 port_explicit: bool = False, background: bool = False) -> None:
    auto_line = _resolve_auto_threshold(profile, settings)
    app = create_app(providers, profile, settings)
    runner = web.AppRunner(app)
    await runner.setup()
    actual_port = await _bind_site(runner, host, port, port_explicit)
    print(f"awerouter listening on {host}:{actual_port}  [{profile.name}]")
    print(f"  protocol      -> {profile.protocol}")
    print("  hot reload    -> on (routing.json/providers.json changes apply without restart)")
    if profile.port is not None:
        print(f"  port          -> {profile.port} (from routing.json; --port overrides)")
    if profile.rtk:
        print("  rtk           -> on (tool-result compression)")
    if profile.odcp is not None:
        print("  odcp          -> on (dedup + errored-call input purge)")
    if profile.awecompress is not None:
        sm = profile.awecompress.summary_model or "flash"
        print(f"  awecompress   -> on (frozen summaries; summary calls via {sm})")
    if settings.image_bridge:
        bd = profile.destinations[settings.image_model]
        print(f"  image bridge  -> on ({bd.provider_name}/{bd.model} transcribes "
              f"history images to text)")
    print(f"  bg            -> {settings.background_model}  "
          f"think -> {settings.think_model}  "
          f"main -> {'auto' if settings.default_model == 'flash' else settings.default_model}")
    if settings.image_model != "pro" or settings.default_model != "flash":
        print(f"  image         -> {settings.image_model}  "
              f"default -> {settings.default_model}")
    tr = settings.tool_routing
    parts = [f"web→{tr.web_search or settings.web_search_model}",
             *(f"{k}→{v}" for k, v in (("edit", tr.edit),) if v)]
    print(f"  tool          -> {'  '.join(parts)}")
    if profile.settings_overrides:
        print(f"  overrides     -> {_overrides_line(profile.settings_overrides)}")
    print(f"  flash  -> {profile.destinations['flash'].provider_name}/{profile.destinations['flash'].model}")
    print(f"  pro    -> {profile.destinations['pro'].provider_name}/{profile.destinations['pro'].model}")
    print(f"  failover      -> flash: {_failover_chain(profile, 'flash')}  |  "
          f"pro: {_failover_chain(profile, 'pro')}")
    if auto_line is not None:
        print(auto_line)
    else:
        print(f"  L3 threshold -> {profile.long_context_threshold}")
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print()
    print(_client_hints(profile.protocols, display_host, actual_port, settings))
    _serve_warnings(_flat_providers(providers), providers)
    try:
        runtime.register(profile.name, profile.protocol, actual_port, host, background)
    except OSError as exc:
        print(f"  warning -> cannot register this instance ({exc}); "
              "awerouter serve status/stop won't see it")
    watcher = asyncio.ensure_future(_watch_config(app, profile.name))
    await _run_until_stopped(runner, watcher)


# ---------------------------------------------------------------------------
# Gateway serve: one daemon, one port, every profile
# ---------------------------------------------------------------------------


def _load_gateway_state() -> tuple[dict[str, _GatewayEntry], "str | None"]:
    """Everything the gateway serves, freshly loaded from disk: one entry per
    profile plus the bare-name default (defaultProfile, or the only profile)."""
    providers_all = load_providers()
    settings, profiles = load_routing()
    if not profiles:
        die("no profiles in routing.json")
    validate_profiles(providers_all, profiles)
    entries = {
        name: _GatewayEntry(
            profile=p,
            settings=p.settings,
            providers={proto: providers_all[proto] for proto in p.protocols},
        )
        for name, p in profiles.items()
    }
    default = settings.default_profile
    if default is None and len(entries) == 1:
        default = next(iter(entries))  # a lone profile is the natural default
    return entries, default


def _gateway_serving_protocols(entries) -> list:
    """Union of served protocols across profiles, first-seen order."""
    return list(dict.fromkeys(p for e in entries.values() for p in e.profile.protocols))


def _gateway_flat_providers(entries) -> dict:
    """Flatten every profile's providers for the serve-start warnings (first
    sighting of a name wins — same rule as _flat_providers)."""
    flat = {}
    for entry in entries.values():
        for group in entry.providers.values():
            for p in group.values():
                flat.setdefault(p.name, p)
    return flat


def _gateway_client_hints(entries, default_profile: "str | None",
                          display_host: str, port: int) -> str:
    """Client pointers for gateway serve. With a default the per-protocol tier
    env hints hold (bare names resolve); without one they would lie, so only
    the base URLs print."""
    protocols = _gateway_serving_protocols(entries)
    if default_profile:
        base = _client_hints(protocols, display_host, port,
                             entries[default_profile].settings)
    else:
        base = "\n\n".join(
            (f"point Claude Code here:\n"
             f"  export ANTHROPIC_BASE_URL=http://{display_host}:{port}")
            if p == "anthropic" else
            (f"point your OpenAI client here:\n"
             f"  export OPENAI_BASE_URL=http://{display_host}:{port}/v1"
             + ('\n  codex: set base_url to the same URL in config.toml '
                '(wire_api = "responses")' if p == "openai-responses" else ""))
            for p in protocols)
    example = f"{sorted(entries)[0]}/auto"
    tail = (f"gateway: the model name picks the profile — "
            f"'<profile>/auto|flash|pro' (e.g. {example}); GET /v1/models lists them")
    if default_profile:
        tail += f"; bare names route to '{default_profile}'"
    else:
        tail += "; no defaultProfile set — bare names are rejected"
    return base + "\n\n" + tail


def _reload_gateway(app) -> bool:
    """Swap a live gateway app's entries/default for freshly loaded ones.

    Same refusal semantics as the single-profile reload: a failed load keeps
    the previous set serving; in-flight requests keep what they captured."""
    try:
        new_entries, new_default = _load_gateway_state()
        for entry in new_entries.values():
            _awecompress_validate(entry.profile, entry.providers)
    except SystemExit as exc:
        print(f"  config reload skipped (serving the previous config): {exc}")
        return False
    # Re-materialize "auto" thresholds for the fresh copies (each prints its
    # own evidence line, same as the single-profile reload).
    for entry in new_entries.values():
        _resolve_auto_threshold(entry.profile, entry.settings)
    old_names = set(app["gateway"])
    app["gateway"] = new_entries
    app["default_profile"] = new_default
    added = sorted(set(new_entries) - old_names)
    removed = sorted(old_names - set(new_entries))
    if added:
        print(f"  profiles added   -> {', '.join(added)}")
    if removed:
        print(f"  profiles removed -> {', '.join(removed)}")
    print(f"  config reloaded -> {len(new_entries)} profile(s); "
          f"default -> {new_default or '(none)'}")
    return True


async def _serve_gateway(host: str, port: int, port_explicit: bool = False,
                         background: bool = False) -> None:
    entries, default_profile = _load_gateway_state()
    # Materialize every "auto" threshold before the socket opens, so no
    # request can race the resolution (same rule as single-profile serve).
    for entry in entries.values():
        _resolve_auto_threshold(entry.profile, entry.settings)
    app = create_gateway_app(entries, default_profile)
    runner = web.AppRunner(app)
    await runner.setup()
    actual_port = await _bind_site(runner, host, port, port_explicit)
    print(f"awerouter listening on {host}:{actual_port}  [gateway: {len(entries)} profile(s)]")
    print("  hot reload    -> on (routing.json/providers.json changes apply without restart)")
    if default_profile:
        print(f"  default       -> {default_profile} (bare model names route here)")
    else:
        print("  default       -> (none; bare model names are rejected — "
              "use '<profile>/auto|flash|pro')")
    for name, e in sorted(entries.items()):
        flash, pro = e.profile.destinations["flash"], e.profile.destinations["pro"]
        line = (f"  {name}  [{e.profile.protocol}]  "
                f"flash={flash.provider_name}/{flash.model}  "
                f"pro={pro.provider_name}/{pro.model}")
        if e.profile.threshold_auto:
            line += "  L3>auto"
        else:
            line += f"  L3>{e.profile.long_context_threshold:,}"
        if e.profile.rtk:
            line += "  rtk"
        if e.profile.odcp is not None:
            line += "  odcp"
        if e.profile.backups:
            line += (f"  fb flash={_failover_chain(e.profile, 'flash')}"
                     f" pro={_failover_chain(e.profile, 'pro')}")
        print(line)
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print()
    print(_gateway_client_hints(entries, default_profile, display_host, actual_port))
    _serve_warnings(_gateway_flat_providers(entries), {
        proto: group for e in entries.values() for proto, group in e.providers.items()
    })
    try:
        runtime.register(GATEWAY_PROFILE_NAME, "+".join(_gateway_serving_protocols(entries)),
                         actual_port, host, background)
    except OSError as exc:
        print(f"  warning -> cannot register this instance ({exc}); "
              "awerouter serve status/stop won't see it")
    watcher = asyncio.ensure_future(_watch_config(app, None))
    await _run_until_stopped(runner, watcher)
