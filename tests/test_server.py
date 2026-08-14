"""Integration tests for awerouter.server."""

import asyncio
import os

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from awerouter.server import create_app
from awerouter.types import Destination, Provider, RoutingProfile


ROUTING = RoutingProfile(
    name="test",
    agent="claude",
    background_model="c1/flash",
    think_model="c1/think",
    long_context_threshold=32,
    destinations={
        "flash": Destination("stepfun", "step-3.5-flash"),
        "pro": Destination("anthropic", "claude-opus-5"),
    },
)


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
            app = create_app(_providers(0), ROUTING)
            async with TestClient(TestServer(app)) as c:
                r = await c.get("/")
                assert r.status == 200
                d = await r.json()
                assert d["name"] == "awerouter"
                assert "POST /v1/messages" in d["endpoints"]
        run(t())

    def test_v1_models(self):
        async def t():
            app = create_app(_providers(0), ROUTING)
            async with TestClient(TestServer(app)) as c:
                r = await c.get("/v1/models")
                assert r.status == 200
                d = await r.json()
                ids = [m["id"] for m in d["data"]]
                assert "c1/flash" in ids
                assert "c1/think" in ids
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
                app = create_app(_providers(up_server.port), ROUTING)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "c1/flash",
                        "messages": [{"content": "hi"}],
                    })
                    assert r.status == 200
                    d = await r.json()
                    assert d["model"] == "step-3.5-flash"
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
                app = create_app(_providers(up_server.port), ROUTING)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "c1/think",
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
                app = create_app(_providers(up_server.port), ROUTING)
                async with TestClient(TestServer(app)) as c:
                    await c.post("/v1/messages", json={
                        "model": "c1/flash",
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
                app = create_app(_providers(up_server.port), ROUTING)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "c1/flash",
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
                app = create_app(_providers(up_server.port), ROUTING)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "c1/flash",
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
                app = create_app(_providers(up_server.port), ROUTING)
                async with TestClient(TestServer(app)) as c:
                    r = await c.post("/v1/messages/count_tokens", json={
                        "model": "c1/flash",
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
                app = create_app(_providers(up_server.port), ROUTING)
                async with TestClient(TestServer(app)) as c:
                    # model=c1/pro, short text -> L3 default -> flash
                    r = await c.post("/v1/messages", json={
                        "model": "c1/pro",
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
                app = create_app(_providers(up_server.port), ROUTING)
                async with TestClient(TestServer(app)) as c:
                    # model=c1/pro, long text -> L3 longContext -> pro
                    r = await c.post("/v1/messages", json={
                        "model": "c1/pro",
                        "messages": [{"content": "x" * 200}],
                    })
                    assert r.status == 200
                    d = await r.json()
                    assert d["model"] == "claude-opus-5"
            finally:
                await up_server.close()
        run(t())
