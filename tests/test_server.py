"""Integration tests for awerouter.server."""

import asyncio
import json
import os
import re
import socket
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from awerouter import __version__
from awerouter.server import (
    _agent_from_ua,
    _claude_login_warning,
    _client_hint,
    _codex_login_warning,
    _codex_proxy,
    _filter_headers,
    _loopback_proxy_warning,
    _noauth_warning,
    _pool_models_warning,
    _reload_config,
    _resolve_auto_threshold,
    _serve,
    create_app,
)
from awerouter.types import Destination, OdcpConfig, Provider, RoutingProfile, Settings


ROUTING = RoutingProfile(
    name="test",
    protocols="anthropic",
    long_context_threshold=32,
    destinations={
        "flash": Destination("stepfun", "step-3.5-flash"),
        "pro": Destination("anthropic", "claude-opus-5"),
    },
)

SETTINGS = Settings()  # defaults: background=flash, think=pro


def _providers(port):
    """Grouped by served protocol (the shape serve passes in): the same mock
    upstream registered under every protocol, so any profile picks a group."""
    os.environ.setdefault("STEPFUN_KEY", "flash-key")
    os.environ.setdefault("ANTHROPIC_KEY", "pro-key")
    def group():
        return {
            "stepfun": Provider("stepfun", f"http://127.0.0.1:{port}", "${STEPFUN_KEY}"),
            "anthropic": Provider("anthropic", f"http://127.0.0.1:{port}", "${ANTHROPIC_KEY}", "x-api-key"),
        }
    return {p: group() for p in ("anthropic", "openai-chat", "openai-responses")}


def run(coro):
    asyncio.run(coro)


@pytest.fixture(autouse=True)
def _tmp_log(tmp_path, monkeypatch):
    monkeypatch.setenv("AWEROUTER_LOG_DIR", str(tmp_path / "logs"))


def test_filtered_headers_are_normalized_for_auth_replacement():
    assert _filter_headers({"X-Api-Key": "dummy-client-key"}) == {
        "x-api-key": "dummy-client-key",
    }


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

    def test_missing_auth_env_returns_503_and_keeps_serving(self, monkeypatch):
        """A ${VAR} with no value used to SystemExit inside the request path,
        killing the whole daemon (a KeepAlive crash loop under launchd). The
        daemon guard middleware must turn it into a 503 for that one request
        and keep serving the next."""
        monkeypatch.delenv("MISSING_KEY_TEST", raising=False)
        async def t():
            providers = _providers(0)
            providers["anthropic"]["stepfun"] = Provider(
                "stepfun", "http://127.0.0.1:1", "${MISSING_KEY_TEST}")

            async def up(request):
                return web.json_response({"model": "pro"})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                providers["anthropic"]["anthropic"] = Provider(
                    "anthropic", f"http://127.0.0.1:{up_server.port}",
                    "${ANTHROPIC_KEY}", "x-api-key")
                app = create_app(providers, ROUTING, SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "flash", "messages": [{"content": "hi"}],
                    })
                    assert r.status == 503
                    d = await r.json()
                    assert "MISSING_KEY_TEST" in d["error"]["message"]
                    # the daemon survived: the next request is served fine
                    r2 = await c.post("/v1/messages", json={
                        "model": "pro", "messages": [{"content": "hi"}],
                    })
                    assert r2.status == 200
            finally:
                await up_server.close()
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

    def test_noauth_provider_sends_no_auth_headers(self):
        """Local no-auth provider: the client's incoming key is dropped,
        nothing is injected — upstream sees a clean request."""
        async def t():
            captured = {}

            async def up(request):
                captured["x_api_key"] = request.headers.get("x-api-key")
                captured["authorization"] = request.headers.get("authorization")
                return web.json_response({"model": "x"})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                providers = _providers(up_server.port)
                providers["anthropic"]["stepfun"] = Provider(
                    "stepfun", f"http://127.0.0.1:{up_server.port}", None)
                app = create_app(providers, ROUTING, SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "flash",
                        "messages": [{"content": "hi"}],
                    }, headers={"x-api-key": "client-key"})
                    assert r.status == 200
                    assert captured["x_api_key"] is None
                    assert captured["authorization"] is None
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
                "anthropic": {"dead": Provider("dead", f"http://127.0.0.1:{dead_port}", "k")},
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


class TestMultiProtocol:
    """"protocol": ["anthropic", "openai-chat"] serves both wire protocols on
    one port — clients pick by endpoint path; the log records which protocol
    actually served the request."""

    def _app(self):
        profile = RoutingProfile("mm", ["anthropic", "openai-chat"], 32, {
            "flash": Destination("stepfun", "sf-flash"),
            "pro": Destination("anthropic", "opus-pro"),
        })
        return create_app(_providers(0), profile, SETTINGS)

    def test_both_protocols_served_on_one_port(self):
        async def t():
            captured = []

            async def up(request):
                captured.append(request.path)
                return web.json_response({"model": "x"})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            # flat base_url (no /v1) + the openai-chat endpoint path
            up_app.router.add_post("/chat/completions", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                providers = _providers(up_server.port)
                profile = RoutingProfile("mm", ["anthropic", "openai-chat"], 32, {
                    "flash": Destination("stepfun", "sf-flash"),
                    "pro": Destination("anthropic", "opus-pro"),
                })
                app = create_app(providers, profile, SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "think", "messages": [{"content": "hi"}],
                    })
                    assert r.status == 200
                    r2 = await c.post("/v1/chat/completions", json={
                        "model": "auto", "messages": [{"role": "user", "content": "hi"}],
                    })
                    assert r2.status == 200
                    # each protocol forwards along its own upstream path
                    assert captured == ["/v1/messages", "/chat/completions"]
            finally:
                await up_server.close()
        run(t())

    def test_unserved_protocol_still_400s(self):
        async def t():
            app = self._app()
            async with TestClient(TestServer(app)) as c:
                r = await c.post("/v1/responses", json={
                    "model": "auto", "input": "hi",
                })
                assert r.status == 400
                d = await r.json()
                assert "speaks 'anthropic+openai-chat'" in d["error"]["message"]
        run(t())

    def test_log_records_serving_protocol(self):
        from awerouter.logging import tail
        async def t():
            app = self._app()
            async with TestClient(TestServer(app)) as c:
                await c.post("/v1/messages", json={
                    "model": "think", "messages": [{"content": "hi"}],
                })
                await c.post("/v1/chat/completions", json={
                    "model": "auto", "messages": [{"role": "user", "content": "hi"}],
                })
            logs = tail(2)
            assert [e.protocol for e in logs] == ["anthropic", "openai-chat"]
        run(t())


class TestCodexAccount:
    """"auth": "codex" providers ride the local Codex CLI login into routing."""

    @pytest.fixture(autouse=True)
    def _codex_home(self, tmp_path, monkeypatch):
        home = tmp_path / "codex-home"
        home.mkdir()
        monkeypatch.setenv("CODEX_HOME", str(home))
        self.home = home

    def _login(self, access_token="tok-1", account_id="acct-1"):
        (self.home / "auth.json").write_text(json.dumps(
            {"tokens": {"access_token": access_token, "account_id": account_id}}),
            encoding="utf-8")

    def _app(self, port):
        providers = {"openai-responses": {
            "codex": Provider("codex", f"http://127.0.0.1:{port}", "codex")}}
        profile = RoutingProfile("cx", "openai-responses", 32, {
            "flash": Destination("codex", "gpt-5.6-luna"),
            "pro": Destination("codex", "gpt-5.6-luna"),
        })
        return create_app(providers, profile, SETTINGS)

    SSE_OK = (
        'event: response.created\n'
        'data: {"type":"response.created","response":{"id":"r1"}}\n\n'
        'event: response.output_item.done\n'
        'data: {"type":"response.output_item.done","item":{"id":"m1","type":"message",'
        '"role":"assistant","content":[{"type":"output_text","text":"pong"}]}}\n\n'
        'event: response.completed\n'
        'data: {"type":"response.completed","response":{"id":"r1","model":"gpt-5.6-luna",'
        '"status":"completed"}}\n\n'
    )

    def test_codex_headers_store_and_model_rewrite(self):
        """Full codex header set replaces the client's dummy key; store is
        forced false (ZDR backend); model is rewritten to the destination's."""
        captured = {}

        async def t():
            async def up(request):
                captured.update({
                    "authorization": request.headers.get("authorization"),
                    "account": request.headers.get("chatgpt-account-id"),
                    "beta": request.headers.get("OpenAI-Beta"),
                    "originator": request.headers.get("originator"),
                })
                body = await request.json()
                captured["body"] = body
                return web.json_response({"model": body["model"]})

            up_app = web.Application()
            up_app.router.add_post("/responses", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                self._login("tok-9", "acct-9")
                async with TestClient(TestServer(self._app(up_server.port))) as c:
                    r = await c.post("/v1/responses", json={
                        "model": "auto",
                        "input": [{"role": "user", "content": "hi"}],
                        "store": True,
                        "stream": True,
                        "max_output_tokens": 4096,
                    }, headers={"authorization": "Bearer dummy-client-key"})
                    assert r.status == 200
            finally:
                await up_server.close()
        run(t())
        assert captured["authorization"] == "Bearer tok-9"
        assert captured["account"] == "acct-9"
        assert captured["beta"] == "responses=experimental"
        assert captured["originator"] == "codex_cli_rs"
        assert captured["body"]["model"] == "gpt-5.6-luna"
        assert captured["body"]["store"] is False
        assert "max_output_tokens" not in captured["body"]  # rejected by the backend

    def test_non_streaming_client_gets_json_response(self):
        """The codex backend only speaks SSE: a non-streaming client's request
        goes upstream as stream=true and comes back as one JSON object."""
        captured = {}
        d = {}

        async def t():
            async def up(request):
                captured["stream"] = (await request.json()).get("stream")
                return web.Response(text=self.SSE_OK, content_type="text/event-stream")

            up_app = web.Application()
            up_app.router.add_post("/responses", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                self._login()
                async with TestClient(TestServer(self._app(up_server.port))) as c:
                    r = await c.post("/v1/responses", json={
                        "model": "auto",
                        "input": [{"role": "user", "content": "hi"}],
                    })
                    assert r.status == 200
                    d.clear()
                    d.update(await r.json())
            finally:
                await up_server.close()
        run(t())
        assert captured["stream"] is True   # forced upstream
        assert d["id"] == "r1"              # buffered back as JSON
        assert d["model"] == "gpt-5.6-luna"
        # output items rebuilt from response.output_item.done (the codex
        # backend's terminal object omits them)
        assert d["output"][0]["content"][0]["text"] == "pong"

    def test_failed_sse_surfaces_error(self):
        d = {}

        async def t():
            sse = ('event: response.failed\n'
                   'data: {"type":"response.failed","response":{"id":"r2",'
                   '"error":{"code":"server_error","message":"boom"}}}\n\n')

            async def up(request):
                return web.Response(text=sse, content_type="text/event-stream")

            up_app = web.Application()
            up_app.router.add_post("/responses", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                self._login()
                async with TestClient(TestServer(self._app(up_server.port))) as c:
                    r = await c.post("/v1/responses", json={
                        "model": "auto",
                        "input": [{"role": "user", "content": "hi"}],
                    })
                    assert r.status == 500
                    d.clear()
                    d.update(await r.json())
            finally:
                await up_server.close()
        run(t())
        assert d["error"]["message"] == "boom"
        from awerouter.logging import tail
        assert tail(1)[0].status == 500

    def test_truncated_sse_logs_client_error_status(self):
        async def t():
            async def up(request):
                return web.Response(
                    text='data: {"type":"response.created","response":{"id":"r3"}}\n\n',
                    content_type="text/event-stream",
                )

            up_app = web.Application()
            up_app.router.add_post("/responses", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                self._login()
                async with TestClient(TestServer(self._app(up_server.port))) as c:
                    r = await c.post("/v1/responses", json={
                        "model": "auto",
                        "input": [{"role": "user", "content": "hi"}],
                    })
                    assert r.status == 502
            finally:
                await up_server.close()
        run(t())
        from awerouter.logging import tail
        assert tail(1)[0].status == 502

    def test_401_rereads_login_once(self):
        """A 401 usually means the CLI refreshed the login under us: re-read
        auth.json and retry the same destination once with the new token."""
        calls = []

        async def t():
            async def up(request):
                calls.append(request.headers.get("authorization"))
                if len(calls) == 1:
                    self._login("tok-2", "acct-1")  # CLI refreshed mid-flight
                    return web.json_response(
                        {"error": {"message": "invalid token"}}, status=401)
                return web.Response(text=self.SSE_OK, content_type="text/event-stream")

            up_app = web.Application()
            up_app.router.add_post("/responses", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                self._login("tok-1", "acct-1")
                async with TestClient(TestServer(self._app(up_server.port))) as c:
                    r = await c.post("/v1/responses", json={
                        "model": "auto",
                        "input": [{"role": "user", "content": "hi"}],
                    })
                    assert r.status == 200
                    assert (await r.json())["id"] == "r1"
            finally:
                await up_server.close()
        run(t())
        assert calls == ["Bearer tok-1", "Bearer tok-2"]
        from awerouter.logging import tail
        e = tail(1)[0]
        assert e.status == 200 and e.codex_retried is True  # retry visible in calibration data

    def test_second_401_falls_back_to_keyed_pro(self, capsys):
        """A 401 that survives the login re-read means the login is dead, not
        refreshed: flash falls back to a keyed pro (loudly, one printed line)
        instead of passing the 401 through."""
        calls = []

        async def t():
            async def up(request):
                calls.append(request.headers.get("authorization"))
                if "chatgpt-account-id" in request.headers:  # codex login request
                    return web.json_response(
                        {"error": {"message": "invalid token"}}, status=401)
                return web.json_response({"id": "r-pro"})  # keyed pro serves it

            up_app = web.Application()
            up_app.router.add_post("/responses", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                self._login("tok-stale", "acct-1")
                providers = {
                    "openai-responses": {
                        "codex": Provider("codex", f"http://127.0.0.1:{up_server.port}", "codex"),
                        "deepseek": Provider("deepseek", f"http://127.0.0.1:{up_server.port}", "sk-x"),
                    },
                }
                profile = RoutingProfile("cx", "openai-responses", 32, {
                    "flash": Destination("codex", "gpt-5.6-luna"),
                    "pro": Destination("deepseek", "deepseek-chat"),
                })
                async with TestClient(TestServer(create_app(providers, profile, SETTINGS))) as c:
                    r = await c.post("/v1/responses", json={
                        "model": "auto",
                        "input": [{"role": "user", "content": "hi"}],
                    })
                    assert r.status == 200
                    assert (await r.json())["id"] == "r-pro"
            finally:
                await up_server.close()
        run(t())
        assert len(calls) == 3  # codex 401, re-read retry 401, keyed pro 200
        assert "fails over to deepseek,deepseek-chat" in capsys.readouterr().out
        from awerouter.logging import tail
        e = tail(1)[0]
        assert e.provider == "deepseek" and e.label.endswith("→fb:deepseek,deepseek-chat")
        assert e.codex_retried is True and e.fallback_hops == 1

    def test_rejected_login_stays_excluded_after_intermediate_failure(self):
        """codex 401x2 -> keyed backup 503 -> the queue's other codex entry
        must NOT be retried: the login was already proven dead on this
        request, so the backup's 503 surfaces instead of another doomed 401."""
        calls = []

        async def t():
            async def up(request):
                calls.append(request.headers.get("authorization", ""))
                if "chatgpt-account-id" in request.headers:  # codex login
                    return web.json_response(
                        {"error": {"message": "invalid token"}}, status=401)
                return web.json_response({"error": "boom"}, status=503)

            up_app = web.Application()
            up_app.router.add_post("/responses", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                self._login("tok-stale", "acct-1")
                providers = {"openai-responses": {
                    "codex": Provider("codex", f"http://127.0.0.1:{up_server.port}", "codex"),
                    "deepseek": Provider("deepseek", f"http://127.0.0.1:{up_server.port}", "sk-x"),
                    "codex2": Provider("codex2", f"http://127.0.0.1:{up_server.port}", "codex"),
                }}
                profile = RoutingProfile("cx", "openai-responses", 32, {
                    "flash": Destination("codex", "gpt-5.6-luna"),
                    "pro": Destination("deepseek", "deepseek-chat"),
                }, backups={"flash": [Destination("deepseek", "deepseek-chat"),
                                      Destination("codex2", "gpt-5.6-luna")]})
                async with TestClient(TestServer(create_app(providers, profile, SETTINGS))) as c:
                    r = await c.post("/v1/responses", json={
                        "model": "auto",
                        "input": [{"role": "user", "content": "hi"}],
                    })
                    assert r.status == 503
            finally:
                await up_server.close()
        run(t())
        # codex 401, re-read retry 401, deepseek 503 — codex2 never contacted
        assert len(calls) == 3

    def test_second_401_surfaces_to_client(self):
        """Both destinations ride the same codex login: a fallback would just
        retry the dead login, so the 401 surfaces after the one re-read."""
        calls = []

        async def t():
            async def up(request):
                calls.append(1)
                return web.json_response(
                    {"error": {"message": "invalid token"}}, status=401)

            up_app = web.Application()
            up_app.router.add_post("/responses", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                self._login("tok-stale", "acct-1")
                async with TestClient(TestServer(self._app(up_server.port))) as c:
                    r = await c.post("/v1/responses", json={
                        "model": "auto",
                        "input": [{"role": "user", "content": "hi"}],
                    })
                    assert r.status == 401
            finally:
                await up_server.close()
        run(t())
        assert len(calls) == 2  # one retry, then the 401 passes through

    def test_missing_login_503_with_hint(self):
        async def t():
            async with TestClient(TestServer(self._app(0))) as c:
                r = await c.post("/v1/responses", json={
                    "model": "auto",
                    "input": [{"role": "user", "content": "hi"}],
                })
                assert r.status == 503
                d = await r.json()
                assert "codex login" in d["error"]["message"]
        run(t())

    def test_serve_warning_without_login(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "absent"))
        providers = {"codex": Provider("codex", "https://chatgpt.com/backend-api/codex", "codex")}
        w = _codex_login_warning(providers)
        assert w is not None and "codex login" in w

    def test_serve_warning_with_invalid_login(self):
        (self.home / "auth.json").write_text("{}", encoding="utf-8")
        providers = {"codex": Provider("codex", "https://chatgpt.com/backend-api/codex", "codex")}
        w = _codex_login_warning(providers)
        assert w is not None and "access_token" in w

    def test_no_serve_warning_with_login(self):
        self._login()
        providers = {"codex": Provider("codex", "https://chatgpt.com/backend-api/codex", "codex")}
        assert _codex_login_warning(providers) is None

    REMOTE = "https://chatgpt.com/backend-api/codex"

    def _proxies(self, monkeypatch, value):
        monkeypatch.setattr("awerouter.server.urllib.request.getproxies", lambda: value)

    def test_proxy_https_env(self, monkeypatch):
        self._proxies(monkeypatch, {"https": "http://127.0.0.1:7890"})
        assert _codex_proxy(self.REMOTE) == "http://127.0.0.1:7890"

    def test_proxy_all_env_fallback(self, monkeypatch):
        self._proxies(monkeypatch, {"all": "http://127.0.0.1:7890"})
        assert _codex_proxy(self.REMOTE) == "http://127.0.0.1:7890"

    def test_loopback_never_proxied(self, monkeypatch):
        """Loopback targets (local relays) stay direct even with proxies set."""
        self._proxies(monkeypatch, {"https": "http://127.0.0.1:7890"})
        assert _codex_proxy("http://127.0.0.1:9/") is None

    def test_socks_only_proxy_ignored(self, monkeypatch):
        self._proxies(monkeypatch, {"all": "socks5://127.0.0.1:7890"})
        assert _codex_proxy(self.REMOTE) is None

    def test_no_proxies_direct(self, monkeypatch):
        self._proxies(monkeypatch, {})
        assert _codex_proxy(self.REMOTE) is None


class TestClaudeAccount:
    """"auth": "claude" providers ride the awerouter-owned OAuth login."""

    @pytest.fixture(autouse=True)
    def _store_dir(self, tmp_path, monkeypatch):
        d = tmp_path / "cfg"
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(d))
        self.dir = d

    def _login(self, access_token="tok-1", refresh_token="rt-1",
               expires_at=None):
        import time as _time
        path = self.dir / "claude-auth.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at if expires_at is not None else _time.time() + 3600,
        }), encoding="utf-8")

    def _app(self, port, flash="claude", pro="claude"):
        providers = {
            "anthropic": {
                "claude": Provider("claude", f"http://127.0.0.1:{port}", "claude"),
                "deepseek": Provider("deepseek", f"http://127.0.0.1:{port}", "sk-x"),
            },
        }
        profile = RoutingProfile("cc", "anthropic", 32, {
            "flash": Destination(flash, "claude-sonnet-4-5"),
            "pro": Destination(pro, "claude-opus-4-5"),
        })
        return create_app(providers, profile, SETTINGS)

    def test_headers_replace_client_key(self):
        """Bearer access token + oauth beta flag replace the client's dummy key."""
        captured = {}

        async def t():
            async def up(request):
                captured.update({
                    "authorization": request.headers.get("authorization"),
                    "beta": request.headers.get("anthropic-beta"),
                    "version": request.headers.get("anthropic-version"),
                    "x_api_key": request.headers.get("x-api-key"),
                })
                body = await request.json()
                captured["body"] = body
                return web.json_response({"model": body["model"]})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                self._login("tok-9")
                async with TestClient(TestServer(self._app(up_server.port))) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "auto",
                        "messages": [{"content": "hi"}],
                    }, headers={"x-api-key": "dummy-client-key"})
                    assert r.status == 200
            finally:
                await up_server.close()
        run(t())
        assert captured["authorization"] == "Bearer tok-9"
        assert captured["x_api_key"] is None
        assert "oauth-2025-04-20" in captured["beta"]
        assert captured["version"] == "2023-06-01"
        assert captured["body"]["model"] == "claude-sonnet-4-5"

    def test_401_forces_refresh_and_retries_once(self, monkeypatch):
        """A 401 triggers one forced token refresh (rotating the store) and a
        retry with the new access token."""
        from awerouter import claude
        calls = []

        async def t():
            async def up(request):
                calls.append(request.headers.get("authorization"))
                if len(calls) == 1:
                    return web.json_response(
                        {"error": {"message": "invalid token"}}, status=401)
                return web.json_response({"model": "ok"})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                self._login("tok-1", "rt-1")
                monkeypatch.setattr(claude, "_token_request", lambda p: {
                    "access_token": "tok-2", "refresh_token": "rt-2", "expires_in": 3600})
                async with TestClient(TestServer(self._app(up_server.port))) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "auto",
                        "messages": [{"content": "hi"}],
                    })
                    assert r.status == 200
            finally:
                await up_server.close()
        run(t())
        assert calls == ["Bearer tok-1", "Bearer tok-2"]
        on_disk = json.loads((self.dir / "claude-auth.json").read_text(encoding="utf-8"))
        assert on_disk["refresh_token"] == "rt-2"  # rotation persisted
        from awerouter.logging import tail
        assert tail(1)[0].codex_retried is True  # 401-retry marker in usage log

    def test_second_401_falls_back_to_keyed_pro(self, capsys, monkeypatch):
        """A 401 that survives the refresh means the login is dead: flash
        falls back to a keyed pro (loudly) instead of passing the 401."""
        from awerouter import claude
        calls = []

        async def t():
            async def up(request):
                calls.append(request.headers.get("authorization"))
                beta = request.headers.get("anthropic-beta") or ""
                if "oauth-2025-04-20" in beta:  # claude login request
                    return web.json_response(
                        {"error": {"message": "invalid token"}}, status=401)
                return web.json_response({"model": "pro-ok"})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                self._login("tok-stale")
                monkeypatch.setattr(claude, "_token_request", lambda p: {
                    "access_token": "tok-dead", "refresh_token": "rt-2", "expires_in": 3600})
                async with TestClient(TestServer(self._app(up_server.port, pro="deepseek"))) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "auto",
                        "messages": [{"content": "hi"}],
                    })
                    assert r.status == 200
                    assert (await r.json())["model"] == "pro-ok"
            finally:
                await up_server.close()
        run(t())
        assert len(calls) == 3  # claude 401, refreshed retry 401, keyed pro 200
        assert "fails over to" in capsys.readouterr().out

    def test_second_401_surfaces_to_client(self, monkeypatch):
        """Both destinations ride the same claude login: a fallback would just
        retry the dead login, so the 401 surfaces after the one refresh."""
        from awerouter import claude
        calls = []

        async def t():
            async def up(request):
                calls.append(1)
                return web.json_response(
                    {"error": {"message": "invalid token"}}, status=401)

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                self._login("tok-stale")
                monkeypatch.setattr(claude, "_token_request", lambda p: {
                    "access_token": "tok-dead", "refresh_token": "rt-2", "expires_in": 3600})
                async with TestClient(TestServer(self._app(up_server.port))) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "auto",
                        "messages": [{"content": "hi"}],
                    })
                    assert r.status == 401
            finally:
                await up_server.close()
        run(t())
        assert len(calls) == 2  # one retry, then the 401 passes through

    def test_missing_login_503_with_hint(self):
        async def t():
            async with TestClient(TestServer(self._app(0))) as c:
                r = await c.post("/v1/messages", json={
                    "model": "auto",
                    "messages": [{"content": "hi"}],
                })
                assert r.status == 503
                d = await r.json()
                assert "awerouter config login claude" in d["error"]["message"]
        run(t())

    def test_serve_warning_without_login(self):
        providers = {"claude": Provider("claude", "https://api.anthropic.com", "claude")}
        w = _claude_login_warning(providers)
        assert w is not None and "awerouter config login claude" in w

    def test_serve_warning_with_invalid_login(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "claude-auth.json").write_text("{}", encoding="utf-8")
        providers = {"claude": Provider("claude", "https://api.anthropic.com", "claude")}
        w = _claude_login_warning(providers)
        assert w is not None and "awerouter config login claude" in w

    def test_no_serve_warning_with_login(self):
        self._login()
        providers = {"claude": Provider("claude", "https://api.anthropic.com", "claude")}
        assert _claude_login_warning(providers) is None

    def test_no_serve_warning_with_stale_login(self):
        """A stale token is fine — it refreshes on the first request, so serve
        start must not warn (and must not touch the network)."""
        import time as _time
        self._login(expires_at=_time.time() - 60)
        providers = {"claude": Provider("claude", "https://api.anthropic.com", "claude")}
        assert _claude_login_warning(providers) is None


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
            protocols="anthropic",
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


class TestNoauthWarning:
    def test_loopback_silent(self):
        providers = {"ollama": Provider("ollama", "http://127.0.0.1:11434", None)}
        assert _noauth_warning(providers) is None

    def test_off_machine_warns(self):
        providers = {"stepfun": Provider("stepfun", "https://api.stepfun.com", None)}
        w = _noauth_warning(providers)
        assert w is not None and "stepfun" in w

    def test_off_machine_with_auth_silent(self):
        providers = {"stepfun": Provider("stepfun", "https://api.stepfun.com", "${K}")}
        assert _noauth_warning(providers) is None


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
            # The next port is only *expected* to be free — a CI runner may
            # hold it with a lingering socket from an earlier test — so assert
            # "scanned up to some free port" rather than exactly port + 1.
            note = re.search(r"using next free port (\d+)", out)
            listen = re.search(r"listening on 127\.0\.0\.1:(\d+)", out)
            assert f"port {port} busy" in out
            assert note and listen
            assert int(note.group(1)) > port
            assert note.group(1) == listen.group(1)
        finally:
            s.close()

    def test_serve_registers_and_unregisters(self):
        from awerouter import runtime

        async def t():
            task = asyncio.ensure_future(
                _serve("127.0.0.1", 0, _providers(0), ROUTING, SETTINGS))
            await asyncio.sleep(0.5)
            inst = runtime.instance_by_pid(os.getpid())
            assert inst is not None
            assert inst["background"] is False
            assert inst["port"] > 0
            task.cancel()
            await task

        asyncio.run(t())
        assert runtime.instance_by_pid(os.getpid()) is None


class TestHotReload:
    """routing.json / providers.json changes swap a live app's routing."""

    def _write_config(self, tmp_path, threshold=8000, port=None):
        (tmp_path / "providers.json").write_text(json.dumps({"anthropic": {
            "stepfun": {"base_url": "https://api.stepfun.com/x", "auth": "${STEPFUN_KEY}"},
            "anthropic": {"base_url": "https://api.anthropic.com", "auth": "${ANTHROPIC_KEY}"},
        }}))
        entry = {"protocol": "anthropic", "longContextThreshold": threshold,
                 "destinations": {"flash": "stepfun,sf-flash", "pro": "anthropic,opus"}}
        if port is not None:
            entry["port"] = port
        (tmp_path / "routing.json").write_text(json.dumps({"cc-1": entry}))

    def _app(self):
        # _reload_config only swaps these three entries; a plain dict is the app
        return {"providers": _providers(0), "profile": ROUTING, "settings": SETTINGS}

    def test_reload_swaps_profile(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        self._write_config(tmp_path, threshold=4000)
        app = self._app()
        assert _reload_config(app, "cc-1") is True
        assert app["profile"].name == "cc-1"
        assert app["profile"].long_context_threshold == 4000
        assert app["settings"] is not SETTINGS  # freshly loaded, not the startup copy
        out = capsys.readouterr().out
        assert "config reloaded" in out
        assert "L3>4,000" in out
        assert "restart serve to rebind" not in out

    def test_reload_announces_port_change_without_rebinding(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        self._write_config(tmp_path, port=20150)
        app = self._app()
        assert _reload_config(app, "cc-1") is True
        assert app["profile"].port == 20150
        assert "restart serve to rebind" in capsys.readouterr().out

    def test_invalid_config_keeps_serving_previous(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        self._write_config(tmp_path)
        (tmp_path / "routing.json").write_text("{ broken json")
        app = self._app()
        old_profile = app["profile"]
        assert _reload_config(app, "cc-1") is False
        assert app["profile"] is old_profile
        assert "reload skipped" in capsys.readouterr().out

    def test_removed_profile_keeps_serving_previous(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        self._write_config(tmp_path)
        (tmp_path / "routing.json").write_text(json.dumps({"settings": {}}))
        app = self._app()
        old_profile = app["profile"]
        assert _reload_config(app, "cc-1") is False
        assert app["profile"] is old_profile
        assert "not found" in capsys.readouterr().out

    def test_watcher_applies_change(self, tmp_path, monkeypatch):
        from awerouter import server
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(server, "_RELOAD_POLL_S", 0.05)
        self._write_config(tmp_path, threshold=8000)
        app = self._app()

        async def t():
            task = asyncio.ensure_future(server._watch_config(app, "cc-1"))
            await asyncio.sleep(0.2)  # let the watcher snapshot the initial mtimes
            self._write_config(tmp_path, threshold=4000)
            for _ in range(50):
                if app["profile"].long_context_threshold == 4000:
                    break
                await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(t())
        assert app["profile"].long_context_threshold == 4000


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


class TestOdcp:
    """odcp pruning: opt-in per profile, runs before rtk, applied before
    upstream forwarding."""

    ARGS = {"command": "cat config.py"}
    # grep output that rtk genuinely shrinks (for the combined-order test)
    GREP = "\n".join(f"src/file{i % 3}.py:{i * 4 + 1}:def helper_{i}()" for i in range(45))
    NOTE = "[odcp: superseded output of an identical earlier tool call removed]"

    def _profile(self, **kw):
        return RoutingProfile("test", "anthropic", 32, {
            "flash": Destination("stepfun", "step-3.5-flash"),
            "pro": Destination("anthropic", "claude-opus-5"),
        }, **kw)

    def _body(self, payload):
        def pair(i):
            return [
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": f"t{i}", "name": "Bash",
                     "input": dict(self.ARGS)}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": f"t{i}", "content": payload}]},
            ]
        return {"model": "auto", "messages": [{"role": "user", "content": "go"},
                                              *pair(1), *pair(2)]}

    def _upstream_capture(self, captured):
        async def up(request):
            body = await request.json()
            results = [b for m in body["messages"] if isinstance(m.get("content"), list)
                       for b in m["content"] if b.get("type") == "tool_result"]
            captured["first"] = results[0]["content"]
            captured["second"] = results[1]["content"]
            return web.json_response({"model": body["model"]})

        up_app = web.Application()
        up_app.router.add_post("/v1/messages", up)
        return TestServer(up_app)

    def _send(self, profile, body, headers=None):
        captured = {}

        async def t():
            up_server = self._upstream_capture(captured)
            await up_server.start_server()
            try:
                app = create_app(_providers(up_server.port), profile, SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json=body, headers=headers or {})
                    assert r.status == 200
            finally:
                await up_server.close()
        run(t())
        return captured

    def test_deduped_before_upstream(self):
        captured = self._send(self._profile(odcp=OdcpConfig()), self._body("x" * 500))
        assert captured["first"] == self.NOTE
        assert captured["second"] == "x" * 500   # newest survives verbatim
        from awerouter.logging import tail
        assert tail(1)[0].odcp_saved > 0

    def test_bypass_header_off(self):
        captured = self._send(self._profile(odcp=OdcpConfig()), self._body("x" * 500),
                              headers={"x-awerouter-token-saver": "off"})
        assert captured["first"] == "x" * 500
        from awerouter.logging import tail
        assert tail(1)[0].odcp_saved == 0

    def test_default_off_is_transparent(self):
        captured = self._send(self._profile(), self._body("x" * 500))
        assert captured["first"] == "x" * 500

    def test_odcp_runs_before_rtk(self):
        captured = self._send(self._profile(rtk=True, odcp=OdcpConfig()),
                              self._body(self.GREP))
        assert captured["first"] == self.NOTE          # odcp dropped the old output
        assert "matches in" in captured["second"]      # rtk compressed the kept one

    def test_count_tokens_pruned_consistently(self):
        captured = {}

        async def t():
            async def up(request):
                body = await request.json()
                results = [b for m in body["messages"] if isinstance(m.get("content"), list)
                           for b in m["content"] if b.get("type") == "tool_result"]
                captured["first"] = results[0]["content"]
                return web.json_response({"token_count": 5})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages/count_tokens", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers(up_server.port),
                                 self._profile(odcp=OdcpConfig()), SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages/count_tokens",
                                     json=self._body("x" * 500))
                    assert r.status == 200
            finally:
                await up_server.close()
        run(t())
        assert captured["first"] == self.NOTE

    def test_serve_banner_mentions_odcp(self, capsys):
        async def t():
            task = asyncio.ensure_future(
                _serve("127.0.0.1", 0, _providers(0), self._profile(odcp=OdcpConfig()),
                       SETTINGS, True))
            await asyncio.sleep(0.5)
            task.cancel()
            await task
        asyncio.run(t())
        out = capsys.readouterr().out
        assert "odcp" in out and "dedup" in out


class TestImageBridge:
    """imageBridge: flash transcribes history images; pro continues the
    session over pure text. A fresh upload still routes to flash natively."""

    IMG = {"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": "aW1nMQ=="}}

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        from awerouter.vision import CAPTIONS
        CAPTIONS._data.clear()

    def _settings(self):
        return Settings(image_model="flash", default_model="pro", image_bridge=True)

    def _profile(self):
        return RoutingProfile("mm", "anthropic", 32000, {
            "flash": Destination("stepfun", "sf-flash"),
            "pro": Destination("anthropic", "opus-pro"),
        })

    @staticmethod
    def _is_caption(body):
        sys = body.get("system")
        return isinstance(sys, str) and "transcribe" in sys.lower()

    def test_new_upload_goes_to_flash_natively(self):
        calls = []

        async def t():
            async def up(request):
                body = await request.json()
                calls.append(body)
                return web.json_response({"model": body["model"]})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers(up_server.port), self._profile(),
                                 self._settings())
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "auto",
                        "messages": [{"role": "user", "content": [
                            self.IMG, {"type": "text", "text": "what is this"}]}],
                    })
                    assert r.status == 200
            finally:
                await up_server.close()
        run(t())
        assert len(calls) == 1          # no caption call for a fresh upload
        assert calls[0]["model"] == "sf-flash"

    def test_followup_bridges_to_pro(self):
        calls = []

        async def t():
            async def up(request):
                body = await request.json()
                calls.append(body)
                if self._is_caption(body):
                    return web.json_response({"content": [
                        {"type": "text", "text": "screenshot: red test failure"}]})
                return web.json_response({"model": body["model"]})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers(up_server.port), self._profile(),
                                 self._settings())
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "auto",
                        "messages": [
                            {"role": "user", "content": [self.IMG]},
                            {"role": "assistant", "content": "ok"},
                            {"role": "user", "content": "what did you see"},
                        ],
                    })
                    assert r.status == 200
            finally:
                await up_server.close()
        run(t())
        assert len(calls) == 2                       # one caption, one main call
        assert self._is_caption(calls[0])
        assert calls[0]["model"] == "sf-flash"
        main = calls[1]
        assert main["model"] == "opus-pro"           # pro took over the session
        first_content = main["messages"][0]["content"]
        assert all(p["type"] != "image" for p in first_content)
        assert "transcribed by sf-flash" in first_content[0]["text"]
        assert "screenshot: red test failure" in first_content[0]["text"]

    def test_caption_cached_across_requests(self):
        calls = []

        async def t():
            async def up(request):
                body = await request.json()
                calls.append(body)
                if self._is_caption(body):
                    return web.json_response({"content": [
                        {"type": "text", "text": "screenshot: red test failure"}]})
                return web.json_response({"model": body["model"]})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers(up_server.port), self._profile(),
                                 self._settings())
                followup = {
                    "model": "auto",
                    "messages": [
                        {"role": "user", "content": [self.IMG]},
                        {"role": "assistant", "content": "ok"},
                        {"role": "user", "content": "what did you see"},
                    ],
                }
                async with TestClient(TestServer(app)) as c:
                    for _ in range(2):
                        r = await c.post("/v1/messages", json=followup)
                        assert r.status == 200
            finally:
                await up_server.close()
        run(t())
        captions = [b for b in calls if self._is_caption(b)]
        mains = [b for b in calls if not self._is_caption(b)]
        assert len(captions) == 1                     # each image transcribed once
        assert len(mains) == 2
        assert all(b["model"] == "opus-pro" for b in mains)

    def test_caption_failure_falls_back_to_flash(self):
        calls = []

        async def t():
            async def up(request):
                body = await request.json()
                calls.append(body)
                if self._is_caption(body):
                    return web.json_response({"error": "captioner down"}, status=500)
                return web.json_response({"model": body["model"]})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers(up_server.port), self._profile(),
                                 self._settings())
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "auto",
                        "messages": [
                            {"role": "user", "content": [self.IMG]},
                            {"role": "assistant", "content": "ok"},
                            {"role": "user", "content": "what did you see"},
                        ],
                    })
                    assert r.status == 200
            finally:
                await up_server.close()
        run(t())
        main = calls[-1]
        assert main["model"] == "sf-flash"            # image guard routed it
        assert self.IMG in main["messages"][0]["content"]  # image kept verbatim

    def test_bridge_off_keeps_legacy_behavior(self):
        calls = []

        async def t():
            async def up(request):
                body = await request.json()
                calls.append(body)
                return web.json_response({"model": body["model"]})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                # the only delta from _settings(): the bridge flag off
                settings = Settings(image_model="flash", default_model="pro")
                app = create_app(_providers(up_server.port), self._profile(), settings)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "auto",
                        "messages": [
                            {"role": "user", "content": [self.IMG]},
                            {"role": "assistant", "content": "ok"},
                            {"role": "user", "content": "what did you see"},
                        ],
                    })
                    assert r.status == 200
            finally:
                await up_server.close()
        run(t())
        assert len(calls) == 1                          # no caption without the flag
        assert calls[0]["model"] == "sf-flash"          # legacy image route

    def test_count_tokens_bridged_consistently(self):
        """/v1/messages/count_tokens sees the same rewritten body the main
        endpoint would send — the client's context estimate stays honest."""
        calls = []

        async def t():
            async def caption_up(request):
                calls.append(await request.json())
                return web.json_response({"content": [
                    {"type": "text", "text": "screenshot: red test failure"}]})

            async def count_up(request):
                body = await request.json()
                calls.append(body)
                return web.json_response({"token_count": 5})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", caption_up)
            up_app.router.add_post("/v1/messages/count_tokens", count_up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers(up_server.port), self._profile(),
                                 self._settings())
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages/count_tokens", json={
                        "model": "auto",
                        "messages": [
                            {"role": "user", "content": [self.IMG]},
                            {"role": "assistant", "content": "ok"},
                            {"role": "user", "content": "what did you see"},
                        ],
                    })
                    assert r.status == 200
                    assert (await r.json())["token_count"] == 5
            finally:
                await up_server.close()
        run(t())
        assert self._is_caption(calls[0])               # flash transcribed the image
        counted = calls[1]
        assert counted["model"] == "opus-pro"           # estimate for the pro route
        first_content = counted["messages"][0]["content"]
        assert all(p["type"] != "image" for p in first_content)
        assert "screenshot: red test failure" in first_content[0]["text"]

    def test_serve_banner_mentions_image_bridge(self, capsys):
        async def t():
            task = asyncio.ensure_future(
                _serve("127.0.0.1", 0, _providers(0), self._profile(),
                       self._settings(), True))
            await asyncio.sleep(0.5)
            task.cancel()
            await task
        asyncio.run(t())
        out = capsys.readouterr().out
        assert "image bridge" in out and "sf-flash" in out


# ---------------------------------------------------------------------------
# Failover queues: quota-aware candidate walk, cooldown, honest exhaustion
# ---------------------------------------------------------------------------

def _providers_multi(port):
    """Three providers (flash/pro/backup) on one mock upstream — the same
    shape as _providers, plus the backup tier's provider."""
    os.environ.setdefault("STEPFUN_KEY", "flash-key")
    os.environ.setdefault("ANTHROPIC_KEY", "pro-key")
    os.environ.setdefault("GLM_KEY", "backup-key")
    def group():
        return {
            "stepfun": Provider("stepfun", f"http://127.0.0.1:{port}", "${STEPFUN_KEY}"),
            "anthropic": Provider("anthropic", f"http://127.0.0.1:{port}", "${ANTHROPIC_KEY}", "x-api-key"),
            "glm": Provider("glm", f"http://127.0.0.1:{port}", "${GLM_KEY}"),
        }
    return {p: group() for p in ("anthropic", "openai-chat", "openai-responses")}


class TestFailoverQueues:
    @pytest.fixture(autouse=True)
    def _clear_cooldown(self):
        from awerouter import server
        server._COOLDOWN_UNTIL.clear()
        yield
        server._COOLDOWN_UNTIL.clear()

    def _profile(self, backups=None):
        return RoutingProfile("t", "anthropic", 32, {
            "flash": Destination("stepfun", "step-3.5-flash"),
            "pro": Destination("anthropic", "claude-opus-5"),
        }, backups=backups or {})

    def test_multi_hop_walks_declared_backups(self):
        """flash 429 -> first backup 429 -> second backup 200: the queue is
        walked in order, every hop stamped into the label."""
        hits = []

        async def t():
            async def up(request):
                body = await request.json()
                hits.append(body["model"])
                if body["model"] in ("step-3.5-flash", "glm-4.7-flash"):
                    return web.json_response({"error": "quota"}, status=429)
                return web.json_response({"model": body["model"], "ok": True})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers_multi(up_server.port), self._profile({
                    "flash": [Destination("glm", "glm-4.7-flash"),
                              Destination("anthropic", "claude-opus-5")],
                }), SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "flash", "messages": [{"content": "hi"}],
                    })
                    assert r.status == 200
                    assert (await r.json())["model"] == "claude-opus-5"
            finally:
                await up_server.close()
        run(t())
        assert hits == ["step-3.5-flash", "glm-4.7-flash", "claude-opus-5"]
        from awerouter.logging import tail
        e = tail(1)[0]
        assert e.fallback_hops == 2
        assert e.label == ("background→fb:glm,glm-4.7-flash"
                           "→fb:anthropic,claude-opus-5")

    def test_exhausted_queue_passes_last_response_through(self):
        """Queue exhausted: the last upstream error reaches the client
        untouched — never swallowed, never rewritten."""
        async def t():
            async def up(request):
                body = await request.json()
                if body["model"] in ("step-3.5-flash", "glm-4.7-flash"):
                    return web.json_response(
                        {"error": {"message": "glm quota exceeded"}}, status=429)
                return web.json_response({"model": body["model"]})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers_multi(up_server.port), self._profile({
                    "flash": [Destination("glm", "glm-4.7-flash")],
                }), SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "flash", "messages": [{"content": "hi"}],
                    })
                    assert r.status == 429
                    assert (await r.json())["error"]["message"] == "glm quota exceeded"
            finally:
                await up_server.close()
        run(t())

    def test_pro_falls_back_to_flash_by_default(self):
        """Zero config on pro: a 429'd pro hands the request to flash — the
        downgrade is loud (label), the request survives."""
        async def t():
            async def up(request):
                body = await request.json()
                if body["model"] == "claude-opus-5":
                    return web.json_response({"error": "quota"}, status=429)
                return web.json_response({"model": body["model"]})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers_multi(up_server.port), self._profile(), SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "pro", "messages": [{"content": "hi"}],
                    })
                    assert r.status == 200
                    assert (await r.json())["model"] == "step-3.5-flash"
            finally:
                await up_server.close()
        run(t())
        from awerouter.logging import tail
        e = tail(1)[0]
        assert e.label == "think→fb:stepfun,step-3.5-flash"
        assert e.destination == "flash" and e.fallback_hops == 1

    def test_all_cooling_probes_primary_anyway(self):
        """Every candidate cooling: the skip is advisory — the primary is
        probed anyway, never walked to the queue's end to die on the last
        candidate."""
        hits = []

        async def t():
            async def up(request):
                body = await request.json()
                hits.append(body["model"])
                return web.json_response({"model": body["model"]})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers_multi(up_server.port), self._profile({
                    "flash": [Destination("glm", "glm-4.7-flash")],
                }), SETTINGS)
                from awerouter import server
                now = time.monotonic()
                for key in (("anthropic", "stepfun", "step-3.5-flash"),
                            ("anthropic", "glm", "glm-4.7-flash"),
                            ("anthropic", "anthropic", "claude-opus-5")):
                    server._COOLDOWN_UNTIL[key] = now + 60
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "flash", "messages": [{"content": "hi"}],
                    })
                    assert r.status == 200
            finally:
                await up_server.close()
        run(t())
        assert hits == ["step-3.5-flash"]   # primary probed, served — no walk

    def test_cooldown_keyed_by_protocol(self):
        """A quota rejection scopes to the protocol group it happened in: the
        same provider name under another group is a different account with
        different health."""
        from awerouter import server
        dest = Destination("a", "m")
        server._COOLDOWN_UNTIL[("anthropic", "a", "m")] = time.monotonic() + 60
        assert server._cooling(dest, "anthropic") is True
        assert server._cooling(dest, "openai-chat") is False

    def test_429_cooldown_skips_candidate_on_next_request(self):
        """Retry-After starts a cooldown: the next request skips the cooling
        candidate instead of re-hitting its 429."""
        hits = []

        async def t():
            async def up(request):
                body = await request.json()
                hits.append(body["model"])
                if body["model"] == "step-3.5-flash":
                    return web.json_response({"error": "quota"}, status=429,
                                             headers={"retry-after": "60"})
                return web.json_response({"model": body["model"]})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                app = create_app(_providers_multi(up_server.port), self._profile(), SETTINGS)
                async with TestClient(TestServer(app)) as c:
                    for _ in range(2):
                        r = await c.post("/v1/messages", json={
                            "model": "flash", "messages": [{"content": "hi"}],
                        })
                        assert r.status == 200
            finally:
                await up_server.close()
        run(t())
        assert hits == ["step-3.5-flash", "claude-opus-5",  # request 1: 429, fail over
                        "claude-opus-5"]                    # request 2: flash cooling, skip

    def test_network_error_502_names_every_candidate(self):
        """All candidates unreachable: the 502 lists the whole chain so the
        operator sees exactly who was tried."""
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]
        s.close()

        async def t():
            app = create_app(_providers_multi(dead_port), self._profile({
                "flash": [Destination("glm", "glm-4.7-flash")],
            }), SETTINGS)
            async with TestClient(TestServer(app)) as c:
                r = await c.post("/v1/messages", json={
                    "model": "flash", "messages": [{"content": "hi"}],
                })
                assert r.status == 502
                msg = (await r.json())["error"]["message"]
                assert "tried stepfun,step-3.5-flash -> glm,glm-4.7-flash" in msg
        run(t())

    def test_serve_banner_prints_failover_chains(self, capsys):
        """The banner names each tier's effective chain — implicit hop marked
        as such, declared backups in order."""
        async def t():
            task = asyncio.ensure_future(_serve(
                "127.0.0.1", 0, _providers_multi(0), self._profile({
                    "flash": [Destination("glm", "glm-4.7-flash")],
                }), SETTINGS, True))
            await asyncio.sleep(0.5)
            task.cancel()
            await task
        asyncio.run(t())
        out = capsys.readouterr().out
        assert "failover" in out
        assert "flash: glm/glm-4.7-flash  |  pro: flash (implicit)" in out


class TestPoolModelsWarning:
    def test_matching_sets_silent(self):
        groups = {"anthropic": {
            f"s-{i}": Provider(f"s-{i}", "http://x", "k",
                               models=("m1", "m2"), pool="stepfun")
            for i in (1, 2)
        }}
        assert _pool_models_warning(groups) is None

    def test_drifting_sets_warn_per_member(self):
        groups = {"anthropic": {
            "s-1": Provider("s-1", "http://x", "k", models=("m1", "m2"), pool="stepfun"),
            "s-2": Provider("s-2", "http://x", "k", models=("m1",), pool="stepfun"),
            "unpooled": Provider("unpooled", "http://x", "k", models=("m9",)),
        }}
        w = _pool_models_warning(groups)
        assert w is not None and "pool 'stepfun'" in w and "s-2: m1" in w

    def test_same_pool_name_in_another_group_is_separate(self):
        groups = {
            "anthropic": {"a": Provider("a", "http://x", "k", models=("m1",), pool="p")},
            "openai-chat": {"b": Provider("b", "http://x", "k", models=("m2",), pool="p")},
        }
        assert _pool_models_warning(groups) is None
