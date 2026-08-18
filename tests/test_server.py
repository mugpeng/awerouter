"""Integration tests for awerouter.server."""

import asyncio
import os
import socket

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from awerouter import __version__
from awerouter.server import (
    _agent_from_ua,
    _client_hint,
    _loopback_proxy_warning,
    _resolve_auto_threshold,
    _serve,
    create_app,
)
from awerouter.types import Destination, Provider, RoutingProfile, Settings


ROUTING = RoutingProfile(
    name="test",
    protocol="anthropic",
    long_context_threshold=32,
    destinations={
        "flash": Destination("stepfun", "step-3.5-flash"),
        "pro": Destination("anthropic", "claude-opus-5"),
    },
)

SETTINGS = Settings()  # defaults: background=flash, think=pro


def _providers(port):
    os.environ.setdefault("STEPFUN_KEY", "flash-key")
    os.environ.setdefault("ANTHROPIC_KEY", "pro-key")
    return {
        "stepfun": Provider("stepfun", f"http://127.0.0.1:{port}", "${STEPFUN_KEY}"),
        "anthropic": Provider("anthropic", f"http://127.0.0.1:{port}", "${ANTHROPIC_KEY}", "x-api-key"),
    }


def run(coro):
    asyncio.run(coro)


@pytest.fixture(autouse=True)
def _tmp_log(tmp_path, monkeypatch):
    monkeypatch.setenv("AWEROUTER_LOG_DIR", str(tmp_path / "logs"))


class TestAwerouter:
    def test_root(self):
        async def t():
            app = create_app(_providers(0), ROUTING, SETTINGS)
            async with TestClient(TestServer(app)) as c:
                r = await c.get("/")
                assert r.status == 200
                d = await r.json()
                assert d["name"] == "awerouter"
                assert d["version"] == __version__  # single-sourced, never hardcoded here
                assert "POST /v1/messages" in d["endpoints"]
                assert "POST /v1/chat/completions" in d["endpoints"]
                assert "POST /v1/responses" in d["endpoints"]
        run(t())

    def test_v1_models(self):
        async def t():
            app = create_app(_providers(0), ROUTING, SETTINGS)
            async with TestClient(TestServer(app)) as c:
                r = await c.get("/v1/models")
                assert r.status == 200
                d = await r.json()
                ids = [m["id"] for m in d["data"]]
                assert "flash" in ids
                assert "auto" in ids
                assert "pro" in ids
        run(t())

    def test_flash_route(self):
        async def t():
            async def up(request):
                body = await request.json()
                return web.json_response({"model": body["model"]})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers(up_server.port), ROUTING, SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "flash",
                        "messages": [{"content": "hi"}],
                    })
                    assert r.status == 200
                    d = await r.json()
                    assert d["model"] == "step-3.5-flash"
            finally:
                await up_server.close()
        run(t())

    def test_request_id_logged(self):
        async def t():
            async def up(request):
                body = await request.json()
                return web.json_response({"model": body["model"]})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers(up_server.port), ROUTING, SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    await c.post("/v1/messages", json={
                        "model": "flash",
                        "messages": [{"content": "hi"}],
                    }, headers={"x-request-id": "client-rid-123"})
                from awerouter.logging import tail
                entries = tail(1)
                assert entries[0].request_id == "client-rid-123"
                assert entries[0].profile == "test"
                assert entries[0].duration_ms >= entries[0].ms  # full duration incl. streaming
            finally:
                await up_server.close()
        run(t())

    def test_protocol_and_agent_logged(self):
        async def t():
            async def up(request):
                body = await request.json()
                return web.json_response({"model": body["model"]})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_app.router.add_post("/chat/completions", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers(up_server.port), ROUTING, SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    await c.post("/v1/messages", json={
                        "model": "flash", "messages": [{"content": "hi"}],
                    }, headers={"User-Agent": "claude-cli/2.0.1 (external, cli)"})

                    profile = RoutingProfile("cx", "openai-chat", 32, {
                        "flash": Destination("stepfun", "sf-flash"),
                        "pro": Destination("anthropic", "gpt-pro"),
                    })
                    app2 = create_app(_providers(up_server.port), profile, SETTINGS)
                    async with TestClient(TestServer(app2)) as c2:
                        await c2.post("/v1/chat/completions", json={
                            "model": "auto", "messages": [{"role": "user", "content": "hi"}],
                        }, headers={"User-Agent": "codex_cli_rs/0.42.0 (Mac OS 24.6.0; arm64)"})
                from awerouter.logging import tail
                entries = tail(2)
                by_profile = {e.profile: e for e in entries}
                assert by_profile["test"].protocol == "anthropic"
                assert by_profile["test"].agent == "claude-code"
                assert by_profile["cx"].protocol == "openai-chat"
                assert by_profile["cx"].agent == "codex"
            finally:
                await up_server.close()
        run(t())

    def test_request_id_generated_when_absent(self):
        async def t():
            async def up(request):
                body = await request.json()
                return web.json_response({"model": body["model"]})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers(up_server.port), ROUTING, SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    await c.post("/v1/messages", json={
                        "model": "flash",
                        "messages": [{"content": "hi"}],
                    })
                from awerouter.logging import tail
                assert tail(1)[0].request_id  # non-empty generated id
            finally:
                await up_server.close()
        run(t())

    def test_pro_route_auth_replaced(self):
        async def t():
            captured = {}

            async def up(request):
                body = await request.json()
                captured["model"] = body["model"]
                captured["x_api_key"] = request.headers.get("x-api-key", "")
                return web.json_response({"model": body["model"]})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers(up_server.port), ROUTING, SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "pro",
                        "messages": [{"content": "think"}],
                    })
                    assert r.status == 200
                    d = await r.json()
                    assert d["model"] == "claude-opus-5"
                    assert captured["x_api_key"] == "pro-key"
            finally:
                await up_server.close()
        run(t())

    def test_flash_auth_bearer_auto_prefixed(self):
        """Authorization header provider gets 'Bearer ' auto-prefixed."""
        async def t():
            captured = {}

            async def up(request):
                captured["authorization"] = request.headers.get("authorization", "")
                return web.json_response({"model": "x"})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers(up_server.port), ROUTING, SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    await c.post("/v1/messages", json={
                        "model": "flash",
                        "messages": [{"content": "hi"}],
                    })
                    # flash provider uses ${STEPFUN_KEY}="flash-key", authorization header
                    # → auto-prefixed to "Bearer flash-key"
                    assert captured["authorization"] == "Bearer flash-key"
            finally:
                await up_server.close()
        run(t())

    def test_streaming_passthrough(self):
        async def t():
            async def up(request):
                body = await request.json()
                model = body.get("model", "?")
                async def gen():
                    for i in range(3):
                        yield f"chunk{i} {model}\n".encode()
                return web.Response(body=gen(), content_type="text/event-stream")

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers(up_server.port), ROUTING, SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "flash",
                        "messages": [{"content": "hi"}],
                        "stream": True,
                    })
                    assert r.status == 200
                    chunks = []
                    async for chunk in r.content.iter_any():
                        chunks.append(chunk.decode())
                    body = "".join(chunks)
                    assert "chunk0 step-3.5-flash" in body
            finally:
                await up_server.close()
        run(t())

    def test_pre_stream_fallback(self):
        async def t():
            calls = []

            async def up(request):
                calls.append(1)
                body = await request.json()
                if len(calls) == 1:
                    return web.json_response({"error": "flash down"}, status=503)
                return web.json_response({"model": body["model"], "fallback": True})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers(up_server.port), ROUTING, SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "flash",
                        "messages": [{"content": "hi"}],
                    })
                    assert r.status == 200
                    d = await r.json()
                    assert d["fallback"] is True
                    assert d["model"] == "claude-opus-5"
                    assert len(calls) == 2
            finally:
                await up_server.close()
        run(t())

    def test_count_tokens(self):
        async def t():
            async def up(request):
                body = await request.json()
                return web.json_response({"token_count": 123, "model": body.get("model")})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages/count_tokens", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers(up_server.port), ROUTING, SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages/count_tokens", json={
                        "model": "flash",
                        "messages": [{"content": "hi"}],
                    })
                    assert r.status == 200
                    d = await r.json()
                    assert d["token_count"] == 123
            finally:
                await up_server.close()
        run(t())

    def test_l3_default_flash(self):
        async def t():
            async def up(request):
                body = await request.json()
                return web.json_response({"model": body["model"]})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers(up_server.port), ROUTING, SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    # model=c1/pro, short text -> L3 default -> flash
                    r = await c.post("/v1/messages", json={
                        "model": "auto",
                        "messages": [{"content": "short"}],
                    })
                    assert r.status == 200
                    d = await r.json()
                    assert d["model"] == "step-3.5-flash"
            finally:
                await up_server.close()
        run(t())

    def test_l3_long_context_pro(self):
        async def t():
            async def up(request):
                body = await request.json()
                return web.json_response({"model": body["model"]})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers(up_server.port), ROUTING, SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    # model=c1/pro, long text -> L3 longContext -> pro
                    r = await c.post("/v1/messages", json={
                        "model": "auto",
                        "messages": [{"content": "x" * 200}],
                    })
                    assert r.status == 200
                    d = await r.json()
                    assert d["model"] == "claude-opus-5"
            finally:
                await up_server.close()
        run(t())

    def test_network_error_returns_502_and_logs(self):
        async def t():
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            dead_port = s.getsockname()[1]
            s.close()

            providers = {
                "dead": Provider("dead", f"http://127.0.0.1:{dead_port}", "k"),
            }
            profile = RoutingProfile("t", "anthropic", 32, {
                "flash": Destination("dead", "m1"),
                "pro": Destination("dead", "m2"),
            })
            app = create_app(providers, profile, SETTINGS)
            async with TestClient(TestServer(app)) as c:
                r = await c.post("/v1/messages", json={
                    "model": "flash",  # L2 background -> flash (dead) -> fallback pro (dead) -> 502
                    "messages": [{"role": "user", "content": "hi"}],
                })
                assert r.status == 502
            from awerouter.logging import tail
            entries = tail(5)
            assert any(e.status == 502 for e in entries), entries
        run(t())


class TestOpenAIProtocols:
    """Same-protocol passthrough for /v1/chat/completions and /v1/responses."""

    def test_chat_completions_routes_and_rewrites_model(self):
        async def t():
            async def up(request):
                body = await request.json()
                return web.json_response({"model": body["model"]})

            up_app = web.Application()
            up_app.router.add_post("/chat/completions", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                profile = RoutingProfile("cx", "openai-chat", 32, {
                    "flash": Destination("stepfun", "sf-flash"),
                    "pro": Destination("anthropic", "gpt-pro"),
                })
                app = create_app(_providers(up_server.port), profile, SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/chat/completions", json={
                        "model": "auto",
                        "messages": [{"role": "user", "content": "hi"}],
                    })
                    assert r.status == 200
                    d = await r.json()
                    assert d["model"] == "sf-flash"
            finally:
                await up_server.close()
        run(t())

    def test_responses_routes_long_context_to_pro(self):
        async def t():
            async def up(request):
                body = await request.json()
                return web.json_response({"model": body["model"]})

            up_app = web.Application()
            up_app.router.add_post("/responses", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                profile = RoutingProfile("cx", "openai-responses", 32, {
                    "flash": Destination("stepfun", "sf-flash"),
                    "pro": Destination("anthropic", "gpt-pro"),
                })
                app = create_app(_providers(up_server.port), profile, SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/responses", json={
                        "model": "auto",
                        "input": [{"role": "user", "content": "x" * 200}],
                    })
                    assert r.status == 200
                    d = await r.json()
                    assert d["model"] == "gpt-pro"
            finally:
                await up_server.close()
        run(t())

    def test_unversioned_alias_routes(self):
        """Clients whose base_url omits /v1 hit /chat/completions directly —
        the alias must route and forward the canonical upstream path."""
        async def t():
            async def up(request):
                body = await request.json()
                return web.json_response({"model": body["model"]})

            up_app = web.Application()
            up_app.router.add_post("/chat/completions", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                profile = RoutingProfile("cx", "openai-chat", 32, {
                    "flash": Destination("stepfun", "sf-flash"),
                    "pro": Destination("anthropic", "gpt-pro"),
                })
                app = create_app(_providers(up_server.port), profile, SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/chat/completions", json={
                        "model": "auto",
                        "messages": [{"role": "user", "content": "hi"}],
                    })
                    assert r.status == 200
                    d = await r.json()
                    assert d["model"] == "sf-flash"
                    r2 = await c.get("/models")
                    assert r2.status == 200
            finally:
                await up_server.close()
        run(t())

    def test_protocol_mismatch_returns_clear_400(self):
        async def t():
            app = create_app(_providers(0), ROUTING, SETTINGS)  # anthropic profile
            async with TestClient(TestServer(app)) as c:
                r = await c.post("/v1/chat/completions", json={
                    "model": "auto",
                    "messages": [{"role": "user", "content": "hi"}],
                })
                assert r.status == 400
                d = await r.json()
                assert "speaks 'anthropic'" in d["error"]["message"]
        run(t())

    def test_count_tokens_mismatch_returns_clear_400(self):
        async def t():
            profile = RoutingProfile("cx", "openai-chat", 32, {
                "flash": Destination("stepfun", "sf-flash"),
                "pro": Destination("anthropic", "gpt-pro"),
            })
            app = create_app(_providers(0), profile, SETTINGS)
            async with TestClient(TestServer(app)) as c:
                r = await c.post("/v1/messages/count_tokens", json={
                    "model": "auto",
                    "messages": [{"content": "hi"}],
                })
                assert r.status == 400
        run(t())


class TestAgentFromUA:
    def test_known_clients(self):
        assert _agent_from_ua("claude-cli/2.0.1 (external, cli)") == "claude-code"
        assert _agent_from_ua("Claude-Code/1.0.33") == "claude-code"
        assert _agent_from_ua("codex_cli_rs/0.42.0 (Mac OS 24.6.0)") == "codex"
        assert _agent_from_ua("opencode/0.16.3") == "opencode"
        assert _agent_from_ua("OpenCode/1.2.3 (go)") == "opencode"

    def test_unknown_falls_back_to_first_token(self):
        assert _agent_from_ua("python-requests/2.31.0") == "python-requests"
        assert _agent_from_ua("curl/8.7.1") == "curl"

    def test_empty(self):
        assert _agent_from_ua("") == ""


class TestClientHint:
    def _settings(self):
        return Settings(background_model="flash", think_model="pro")

    def test_openai_chat_is_generic(self):
        """openai-chat serves non-codex agents; the hint carries no codex
        advice (codex needs an openai-responses profile — see README)."""
        hint = _client_hint("openai-chat", "127.0.0.1", 20128, self._settings())
        assert "codex" not in hint
        assert "OPENAI_BASE_URL=http://127.0.0.1:20128/v1" in hint

    def test_openai_responses_keeps_codex_hint(self):
        hint = _client_hint("openai-responses", "127.0.0.1", 20128, self._settings())
        assert 'wire_api = "responses"' in hint
        assert "OPENAI_BASE_URL=http://127.0.0.1:20128/v1" in hint

    def test_anthropic_hint_unchanged(self):
        hint = _client_hint("anthropic", "127.0.0.1", 20128, self._settings())
        assert "ANTHROPIC_BASE_URL=http://127.0.0.1:20128" in hint


class TestResolveAutoThreshold:
    """longContextThreshold: "auto" materializes at serve start from the
    profile's own log (module autouse fixture isolates AWEROUTER_LOG_DIR)."""

    def _auto_profile(self):
        return RoutingProfile(
            name="test",
            protocol="anthropic",
            long_context_threshold=8000,
            destinations={
                "flash": Destination("stepfun", "step-3.5-flash"),
                "pro": Destination("anthropic", "claude-opus-5"),
            },
            threshold_auto=True,
        )

    def _seed_l3(self, tokens):
        from datetime import datetime, timezone
        from awerouter.logging import append
        from awerouter.types import RequestLog
        ts = datetime.now(timezone.utc).isoformat()
        for i, t in enumerate(tokens):
            append(RequestLog(
                ts=ts, request_id=f"r{i}", model_in="auto", label="default",
                destination="flash", provider="stepfun", model_out="sf-flash",
                status=200, ms=1, bytes=1, token_count=t, profile="test",
                protocol="anthropic", agent="claude-code",
            ))

    def test_manual_profile_untouched(self):
        line = _resolve_auto_threshold(ROUTING, SETTINGS)
        assert line is None
        assert ROUTING.long_context_threshold == 32

    def test_resolves_from_own_log(self):
        profile = self._auto_profile()
        self._seed_l3(list(range(100, 10001, 100)))  # 100 samples, 100..10,000
        line = _resolve_auto_threshold(profile, SETTINGS)
        assert line is not None
        assert "p95 of 100 L3 requests" in line
        assert profile.long_context_threshold == 9500

    def test_insufficient_samples_keeps_fallback(self):
        profile = self._auto_profile()
        self._seed_l3([100, 200])
        line = _resolve_auto_threshold(profile, SETTINGS)
        assert "fallbackThreshold 8,000" in line
        assert profile.long_context_threshold == 8000


class TestLoopbackProxyWarning:
    _PROXY_VARS = ("http_proxy", "HTTP_PROXY", "https_proxy",
                   "HTTPS_PROXY", "all_proxy", "ALL_PROXY",
                   "no_proxy", "NO_PROXY")

    def _clear_env(self, monkeypatch):
        for k in self._PROXY_VARS:
            monkeypatch.delenv(k, raising=False)

    def test_no_proxy_env(self, monkeypatch):
        self._clear_env(monkeypatch)
        assert _loopback_proxy_warning() is None

    def test_proxy_without_loopback_exemption_warns(self, monkeypatch):
        self._clear_env(monkeypatch)
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:7890")
        w = _loopback_proxy_warning()
        assert w is not None and "no_proxy" in w

    def test_proxy_with_no_proxy_loopback_ok(self, monkeypatch):
        self._clear_env(monkeypatch)
        monkeypatch.setenv("http_proxy", "http://127.0.0.1:7890")
        monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
        assert _loopback_proxy_warning() is None


class TestServePortBinding:
    """Explicit ports die on conflict; the implicit default scans upward
    from the requested port (20128, 20129, ...) in start order."""

    def _occupied_port(self, port=0):
        s = socket.socket()
        s.bind(("127.0.0.1", port))
        s.listen(1)
        return s, s.getsockname()[1]

    def _serve_briefly(self, port, **kwargs):
        """Run _serve briefly, cancel, return captured stdout."""
        async def t():
            task = asyncio.ensure_future(
                _serve("127.0.0.1", port, _providers(0), ROUTING, SETTINGS, **kwargs))
            await asyncio.sleep(0.5)
            task.cancel()
            await task
        asyncio.run(t())

    def test_explicit_port_conflict_dies(self):
        s, port = self._occupied_port()
        try:
            with pytest.raises(SystemExit, match="already in use"):
                asyncio.run(_serve("127.0.0.1", port, _providers(0), ROUTING, SETTINGS,
                                   port_explicit=True))
        finally:
            s.close()

    def test_implicit_default_taken_when_free(self, capsys):
        s, free = self._occupied_port()
        s.close()  # release; serve should take exactly this port back
        self._serve_briefly(free)
        out = capsys.readouterr().out
        assert f"listening on 127.0.0.1:{free}" in out
        assert "using next free port" not in out

    def test_implicit_port_increments_when_busy(self, capsys):
        s, port = self._occupied_port()
        try:
            self._serve_briefly(port)
            out = capsys.readouterr().out
            assert f"port {port} busy; using next free port {port + 1}" in out
            assert f"listening on 127.0.0.1:{port + 1}" in out
        finally:
            s.close()


class TestRtk:
    """rtk compression: opt-in per profile, applied before upstream forwarding."""

    # grep output that genuinely shrinks under the grep filter (few files, many hits)
    PAYLOAD = "\n".join(f"src/file{i % 3}.py:{i * 4 + 1}:def helper_{i}()" for i in range(45))

    def _rtk_profile(self):
        return RoutingProfile("test", "anthropic", 32, {
            "flash": Destination("stepfun", "step-3.5-flash"),
            "pro": Destination("anthropic", "claude-opus-5"),
        }, rtk=True)

    def _body(self):
        return {"model": "auto", "messages": [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": self.PAYLOAD},
            ]},
        ]}

    def _upstream_capture(self, captured: dict):
        async def up(request):
            body = await request.json()
            captured["content"] = body["messages"][0]["content"][0]["content"]
            return web.json_response({"model": body["model"]})

        up_app = web.Application()
        up_app.router.add_post("/v1/messages", up)
        return TestServer(up_app)

    def test_compressed_before_upstream(self):
        captured = {}

        async def t():
            up_server = self._upstream_capture(captured)
            await up_server.start_server()
            try:
                app = create_app(_providers(up_server.port), self._rtk_profile(), SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json=self._body())
                    assert r.status == 200
            finally:
                await up_server.close()
        run(t())
        assert captured["content"] != self.PAYLOAD
        assert "matches in" in captured["content"]  # grep filter output shape
        from awerouter.logging import tail
        assert tail(1)[0].rtk_saved > 0

    def test_bypass_header_off(self):
        captured = {}

        async def t():
            up_server = self._upstream_capture(captured)
            await up_server.start_server()
            try:
                app = create_app(_providers(up_server.port), self._rtk_profile(), SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json=self._body(),
                                     headers={"x-awerouter-token-saver": "off"})
                    assert r.status == 200
            finally:
                await up_server.close()
        run(t())
        assert captured["content"] == self.PAYLOAD
        from awerouter.logging import tail
        assert tail(1)[0].rtk_saved == 0

    def test_default_off_is_transparent(self):
        captured = {}

        async def t():
            up_server = self._upstream_capture(captured)
            await up_server.start_server()
            try:
                app = create_app(_providers(up_server.port), ROUTING, SETTINGS)  # rtk not set
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json=self._body())
                    assert r.status == 200
            finally:
                await up_server.close()
        run(t())
        assert captured["content"] == self.PAYLOAD

    def test_count_tokens_compressed_consistently(self):
        captured = {}

        async def t():
            async def up(request):
                body = await request.json()
                captured["content"] = body["messages"][0]["content"][0]["content"]
                return web.json_response({"token_count": 5})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages/count_tokens", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers(up_server.port), self._rtk_profile(), SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages/count_tokens", json=self._body())
                    assert r.status == 200
            finally:
                await up_server.close()
        run(t())
        assert captured["content"] != self.PAYLOAD
        assert "matches in" in captured["content"]

    def test_serve_banner_mentions_rtk(self, capsys):
        async def t():
            task = asyncio.ensure_future(
                _serve("127.0.0.1", 0, _providers(0), self._rtk_profile(), SETTINGS, True))
            await asyncio.sleep(0.5)
            task.cancel()
            await task
        asyncio.run(t())
        out = capsys.readouterr().out
        assert "rtk" in out and "tool-result compression" in out
