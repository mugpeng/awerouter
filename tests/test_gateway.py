"""Gateway mode tests: one port, every profile, model name picks the profile.

`awerouter serve all` serves all routing.json profiles on a single port.
Requests address a profile as '<profile>/auto|flash|pro'; bare tier names go
to defaultProfile (or the only profile). These tests build the gateway app
directly (create_gateway_app) the way _load_gateway_state does.
"""

import asyncio
import json
import os

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from awerouter import __version__
from awerouter.config import format_routing_display, load_providers, load_routing, validate_profiles
from awerouter.server import (
    _GatewayEntry,
    _gateway_client_hints,
    _load_gateway_state,
    _reload_gateway,
    create_gateway_app,
)
from awerouter.types import Destination, Provider, RoutingProfile, Settings

PROTOCOLS = ("anthropic", "openai-chat", "openai-responses")


def run(coro):
    asyncio.run(coro)


@pytest.fixture(autouse=True)
def _tmp_log(tmp_path, monkeypatch):
    monkeypatch.setenv("AWEROUTER_LOG_DIR", str(tmp_path / "logs"))
    os.environ.setdefault("STEPFUN_KEY", "flash-key")
    os.environ.setdefault("ANTHROPIC_KEY", "pro-key")


def _providers(port):
    """The same mock upstream registered under every protocol (the shape
    _load_gateway_state builds per profile)."""
    def group():
        return {
            "stepfun": Provider("stepfun", f"http://127.0.0.1:{port}", "${STEPFUN_KEY}"),
            "anthropic": Provider("anthropic", f"http://127.0.0.1:{port}", "${ANTHROPIC_KEY}", "x-api-key"),
        }
    return {p: group() for p in PROTOCOLS}


def _entry(name, protocols, flash_model, pro_model, threshold=32, settings=None,
           port=0):
    profile = RoutingProfile(
        name=name, protocols=protocols, long_context_threshold=threshold,
        destinations={
            "flash": Destination("stepfun", flash_model),
            "pro": Destination("anthropic", pro_model),
        },
        settings=settings or Settings(),
    )
    return _GatewayEntry(
        profile=profile, settings=profile.settings,
        providers={p: _providers(port)[p] for p in profile.protocols},
    )


def _glm_deep(port=0):
    return {
        "glm": _entry("glm", "anthropic", "glm-flash", "glm-pro", port=port),
        "deep": _entry("deep", "anthropic", "deep-flash", "deep-pro", port=port),
    }


def _direct_providers(port=0):
    """Full providers map with declared 'models' on some providers (the second
    arg create_gateway_app now takes: the direct-model registry)."""
    def group():
        return {
            "stepfun": Provider("stepfun", f"http://127.0.0.1:{port}", "${STEPFUN_KEY}",
                                models=("step-3.7-flash", "step-router-v1")),
            "glm": Provider("glm", f"http://127.0.0.1:{port}", "${ANTHROPIC_KEY}",
                            models=("glm-5.3",)),
        }
    return {p: group() for p in PROTOCOLS}


def _mock_upstream(routes):
    """Start one mock upstream answering every path in routes with the JSON
    body's model echoed back. Returns the started TestServer."""
    async def up(request):
        body = await request.json()
        return web.json_response({"model": body["model"]})

    app = web.Application()
    for path in routes:
        app.router.add_post(path, up)
    return app


class TestGatewayModels:
    def test_v1_models_lists_every_profile(self):
        entries = {
            "step-glm": _entry("step-glm", "anthropic", "sf-flash", "glm-pro"),
            "step-codex": _entry("step-codex", "openai-responses", "sf-flash", "gpt-pro"),
        }
        async def t():
            async with TestClient(TestServer(create_gateway_app(entries, "step-glm"))) as c:
                r = await c.get("/v1/models")
                assert r.status == 200
                ids = [m["id"] for m in (await r.json())["data"]]
                # bare tiers of the default profile first, then per-profile tiers
                assert ids[:3] == ["flash", "auto", "pro"]
                assert "step-glm/auto" in ids and "step-glm/pro" in ids
                assert "step-codex/auto" in ids and "step-codex/flash" in ids
        run(t())

    def test_v1_models_without_default_lists_aliases_only(self):
        entries = {
            "a": _entry("a", "anthropic", "f", "p"),
            "b": _entry("b", "anthropic", "f", "p"),
        }
        async def t():
            async with TestClient(TestServer(create_gateway_app(entries, None))) as c:
                ids = [m["id"] for m in (await (await c.get("/v1/models")).json())["data"]]
                assert "auto" not in ids  # no default: bare names are not routable
                assert "a/auto" in ids and "b/auto" in ids
        run(t())

    def test_root_advertises_gateway_mode(self):
        entries = {"a": _entry("a", "anthropic", "f", "p")}
        async def t():
            async with TestClient(TestServer(create_gateway_app(entries, None))) as c:
                d = await (await c.get("/")).json()
                assert d["mode"] == "gateway"
                assert d["models"] == ["a"]
                assert d["version"] == __version__
        run(t())

    def test_v1_models_lists_direct_providers(self):
        """Declared provider/models ('provider/model') show up in /v1/models."""
        entries = {"glm": _entry("glm", "anthropic", "f", "p")}
        async def t():
            async with TestClient(TestServer(create_gateway_app(entries, "glm",
                                                                _direct_providers()))) as c:
                ids = [m["id"] for m in (await (await c.get("/v1/models")).json())["data"]]
                assert "stepfun/step-3.7-flash" in ids
                assert "stepfun/step-router-v1" in ids
                assert "glm/glm-5.3" in ids
        run(t())


class TestGatewayDirectModels:
    """'provider/model' (a provider with declared 'models') forwards straight
    to that provider+model, bypassing the routing pipeline and flash→pro
    fallback. Existing '<profile>/tier' names keep their behavior."""

    def _app(self, up_port, entries=None):
        return create_gateway_app(entries or {"glm": _entry("glm", "anthropic", "f", "p")},
                                  "glm", _direct_providers(up_port))

    def test_direct_model_forwards_to_that_provider(self):
        async def t():
            up_server = TestServer(_mock_upstream(["/v1/messages"]))
            await up_server.start_server()
            try:
                async with TestClient(TestServer(self._app(up_server.port))) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "stepfun/step-3.7-flash",
                        "messages": [{"content": "hi"}],
                    })
                    assert r.status == 200
                    assert (await r.json())["model"] == "step-3.7-flash"
            finally:
                await up_server.close()
        run(t())

    def test_direct_model_ignores_auto_routing(self):
        """A long request to a direct model still hits that model, not pro."""
        async def t():
            up_server = TestServer(_mock_upstream(["/v1/messages"]))
            await up_server.start_server()
            try:
                async with TestClient(TestServer(self._app(up_server.port))) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "stepfun/step-3.7-flash",
                        "messages": [{"content": "x" * 2000}],
                    })
                    assert r.status == 200
                    assert (await r.json())["model"] == "step-3.7-flash"
            finally:
                await up_server.close()
        run(t())

    def test_direct_model_is_addressable_without_default_profile(self):
        """Direct models resolve even with no defaultProfile set."""
        entries = {
            "glm": _entry("glm", "anthropic", "f", "p"),
            "deep": _entry("deep", "anthropic", "f", "p"),
        }
        async def t():
            up_server = TestServer(_mock_upstream(["/v1/messages"]))
            await up_server.start_server()
            try:
                async with TestClient(TestServer(create_gateway_app(entries, None,
                                                                    _direct_providers(up_server.port)))) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "stepfun/step-3.7-flash", "messages": [{"content": "hi"}],
                    })
                    assert r.status == 200
                    assert (await r.json())["model"] == "step-3.7-flash"
            finally:
                await up_server.close()
        run(t())

    def test_unknown_provider_400_lists_profiles(self):
        async def t():
            async with TestClient(TestServer(self._app(0))) as c:
                r = await c.post("/v1/messages", json={
                    "model": "nope/x", "messages": [{"content": "hi"}],
                })
                assert r.status == 400
                msg = (await r.json())["error"]["message"]
                assert "unknown" in msg.lower()
        run(t())

    def test_undeclared_model_400(self):
        async def t():
            async with TestClient(TestServer(self._app(0))) as c:
                r = await c.post("/v1/messages", json={
                    "model": "stepfun/not-declared", "messages": [{"content": "hi"}],
                })
                assert r.status == 400
                assert "step-3.7-flash" in (await r.json())["error"]["message"]
        run(t())

    def test_profile_alias_takes_precedence_over_provider_name(self):
        """A name matching a profile routes as a profile, not a direct provider,
        even if a provider shares the name."""
        async def t():
            up_server = TestServer(_mock_upstream(["/v1/messages"]))
            await up_server.start_server()
            try:
                entries = {"stepfun": _entry("stepfun", "anthropic", "sf-flash", "sf-pro")}
                async with TestClient(TestServer(create_gateway_app(entries, "stepfun",
                                                                   _direct_providers(up_server.port)))) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "stepfun/auto", "messages": [{"content": "hi"}],
                    })
                    assert r.status == 200
                    assert (await r.json())["model"] == "sf-flash"
            finally:
                await up_server.close()
        run(t())

    def test_direct_forward_logs_under_no_profile(self):
        """A direct forward belongs to no routing profile — the usage log
        attributes it to 'direct', not to the context profile that happens
        to serve the protocol."""
        async def t():
            up_server = TestServer(_mock_upstream(["/v1/messages"]))
            await up_server.start_server()
            try:
                async with TestClient(TestServer(self._app(up_server.port))) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "stepfun/step-3.7-flash", "messages": [{"content": "hi"}],
                    })
                    assert r.status == 200
            finally:
                await up_server.close()
        run(t())
        from awerouter.logging import tail
        e = tail(1)[0]
        assert e.profile == "direct"
        assert e.destination == "direct"
        assert e.provider == "stepfun"
        assert e.model_out == "step-3.7-flash"


class TestGatewayRouting:
    """The alias picks the profile; the tier maps onto that profile's own
    L2 labels, so forcing works even with customized tier names."""

    def test_alias_auto_runs_pipeline_short_goes_flash(self):
        async def t():
            up_server = TestServer(_mock_upstream(["/v1/messages"]))
            await up_server.start_server()
            try:
                entries = _glm_deep(up_server.port)
                async with TestClient(TestServer(create_gateway_app(entries, "glm"))) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "deep/auto", "messages": [{"content": "short"}],
                    })
                    assert r.status == 200
                    assert (await r.json())["model"] == "deep-flash"
            finally:
                await up_server.close()
        run(t())

    def test_alias_auto_long_context_goes_pro(self):
        async def t():
            up_server = TestServer(_mock_upstream(["/v1/messages"]))
            await up_server.start_server()
            try:
                entries = _glm_deep(up_server.port)
                async with TestClient(TestServer(create_gateway_app(entries, "glm"))) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "glm/auto", "messages": [{"content": "x" * 200}],
                    })
                    assert r.status == 200
                    assert (await r.json())["model"] == "glm-pro"
            finally:
                await up_server.close()
        run(t())

    def test_alias_pro_forces_pro_with_custom_labels(self):
        """thinkModel=opus-tier: '/pro' still forces the pro destination —
        the tier normalizes to the profile's own L2 label."""
        entries = {
            "glm": _entry("glm", "anthropic", "glm-flash", "glm-pro",
                          settings=Settings(background_model="haiku", think_model="opus-tier")),
        }
        async def t():
            up_server = TestServer(_mock_upstream(["/v1/messages"]))
            await up_server.start_server()
            try:
                for group in entries["glm"].providers.values():
                    for pname, p in group.items():
                        group[pname] = Provider(
                            pname, f"http://127.0.0.1:{up_server.port}", p.auth, p.auth_header)
                async with TestClient(TestServer(create_gateway_app(entries, "glm"))) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "glm/pro", "messages": [{"content": "hi"}],
                    })
                    assert r.status == 200
                    assert (await r.json())["model"] == "glm-pro"
                    # the profile's own tier labels work as tiers too
                    r2 = await c.post("/v1/messages", json={
                        "model": "glm/haiku", "messages": [{"content": "hi"}],
                    })
                    assert (await r2.json())["model"] == "glm-flash"
            finally:
                await up_server.close()
        run(t())

    def test_bare_name_routes_to_default(self):
        async def t():
            up_server = TestServer(_mock_upstream(["/v1/messages"]))
            await up_server.start_server()
            try:
                entries = _glm_deep(up_server.port)
                async with TestClient(TestServer(create_gateway_app(entries, "deep"))) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "pro", "messages": [{"content": "hi"}],  # bare think label
                    })
                    assert r.status == 200
                    assert (await r.json())["model"] == "deep-pro"
            finally:
                await up_server.close()
        run(t())

    def test_single_profile_is_the_implicit_default(self):
        entries = {"only": _entry("only", "anthropic", "of", "op")}
        async def t():
            async with TestClient(TestServer(create_gateway_app(entries, None))) as c:
                r = await c.post("/v1/messages", json={
                    "model": "auto", "messages": [{"content": "hi"}],
                })
                # dead upstream (port 0) -> 502 after fallback, but the bare
                # name itself resolved (a resolution failure would be a 400)
                assert r.status == 502
        run(t())

    def test_multi_protocol_profile_serves_each_endpoint(self):
        entries = {"mm": _entry("mm", ["anthropic", "openai-chat"], "mm-f", "mm-p")}
        async def t():
            up_server = TestServer(_mock_upstream(["/v1/messages", "/chat/completions"]))
            await up_server.start_server()
            try:
                for group in entries["mm"].providers.values():
                    for pname, p in group.items():
                        group[pname] = Provider(
                            pname, f"http://127.0.0.1:{up_server.port}", p.auth, p.auth_header)
                async with TestClient(TestServer(create_gateway_app(entries, "mm"))) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "mm/pro", "messages": [{"content": "hi"}],
                    })
                    assert (await r.json())["model"] == "mm-p"
                    r2 = await c.post("/v1/chat/completions", json={
                        "model": "mm/pro", "messages": [{"role": "user", "content": "hi"}],
                    })
                    assert (await r2.json())["model"] == "mm-p"
            finally:
                await up_server.close()
        run(t())

    def test_log_records_the_alias_and_profile(self):
        async def t():
            up_server = TestServer(_mock_upstream(["/v1/messages"]))
            await up_server.start_server()
            try:
                entries = _glm_deep(up_server.port)
                async with TestClient(TestServer(create_gateway_app(entries, "glm"))) as c:
                    await c.post("/v1/messages", json={
                        "model": "deep/flash", "messages": [{"content": "hi"}],
                    })
            finally:
                await up_server.close()
        run(t())
        from awerouter.logging import tail
        e = tail(1)[0]
        assert e.profile == "deep"
        assert e.model_in == "deep/flash"  # the alias, not the rewritten tier
        assert e.model_out == "deep-flash"


class TestGatewayErrors:
    def _entries(self):
        return {
            "glm": _entry("glm", ["anthropic", "openai-chat"], "glm-flash", "glm-pro"),
            "codex": _entry("codex", "openai-responses", "cx-flash", "cx-pro"),
        }

    def test_unknown_profile_400_lists_available(self):
        async def t():
            async with TestClient(TestServer(create_gateway_app(self._entries(), "glm"))) as c:
                r = await c.post("/v1/messages", json={
                    "model": "nope/auto", "messages": [{"content": "hi"}],
                })
                assert r.status == 400
                msg = (await r.json())["error"]["message"]
                assert "unknown profile 'nope'" in msg
                assert "glm" in msg and "codex" in msg
        run(t())

    def test_protocol_mismatch_names_serving_profiles(self):
        async def t():
            async with TestClient(TestServer(create_gateway_app(self._entries(), "glm"))) as c:
                r = await c.post("/v1/messages", json={
                    "model": "codex/auto", "messages": [{"content": "hi"}],
                })
                assert r.status == 400
                msg = (await r.json())["error"]["message"]
                assert "speaks 'openai-responses'" in msg
                assert "glm" in msg  # the profile that DOES serve anthropic
        run(t())

    def test_bad_tier_400(self):
        async def t():
            async with TestClient(TestServer(create_gateway_app(self._entries(), "glm"))) as c:
                r = await c.post("/v1/messages", json={
                    "model": "glm/turbo", "messages": [{"content": "hi"}],
                })
                assert r.status == 400
                assert "tier" in (await r.json())["error"]["message"]
        run(t())

    def test_bare_name_without_default_400(self):
        entries = {
            "a": _entry("a", "anthropic", "f", "p"),
            "b": _entry("b", "anthropic", "f", "p"),
        }
        async def t():
            async with TestClient(TestServer(create_gateway_app(entries, None))) as c:
                r = await c.post("/v1/messages", json={
                    "model": "auto", "messages": [{"content": "hi"}],
                })
                assert r.status == 400
                assert "defaultProfile" in (await r.json())["error"]["message"]
        run(t())

    def test_default_profile_wrong_protocol_400(self):
        entries = {
            "glm": _entry("glm", "anthropic", "f", "p"),
            "cx": _entry("cx", "openai-responses", "f", "p"),
        }
        async def t():
            async with TestClient(TestServer(create_gateway_app(entries, "cx"))) as c:
                r = await c.post("/v1/messages", json={
                    "model": "auto", "messages": [{"content": "hi"}],
                })
                assert r.status == 400
                assert "default profile" in (await r.json())["error"]["message"]
        run(t())


class TestGatewayCountTokens:
    def test_count_tokens_resolves_via_alias(self):
        async def t():
            captured = {}

            async def up(request):
                body = await request.json()
                captured["model"] = body.get("model")
                return web.json_response({"input_tokens": 5})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages/count_tokens", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                entries = {"glm": _entry("glm", "anthropic", "glm-flash", "glm-pro",
                                         port=up_server.port)}
                async with TestClient(TestServer(create_gateway_app(entries, None))) as c:
                    r = await c.post("/v1/messages/count_tokens", json={
                        "model": "glm/pro", "messages": [{"content": "hi"}],
                    })
                    assert r.status == 200
                    assert captured["model"] == "glm-pro"  # rewritten, not the alias
            finally:
                await up_server.close()
        run(t())


class TestGatewayLoadState:
    """_load_gateway_state reads routing.json into entries + default."""

    def _write(self, tmp_path, routing):
        (tmp_path / "providers.json").write_text(json.dumps({"anthropic": {
            "stepfun": {"base_url": "https://x", "auth": "${K}"},
            "anthropic": {"base_url": "https://y", "auth": "${K}"},
        }}))
        (tmp_path / "routing.json").write_text(json.dumps(routing))

    def _profile(self, threshold=8000):
        return {"protocol": "anthropic", "longContextThreshold": threshold,
                "destinations": {"flash": "stepfun,sf", "pro": "anthropic,op"}}

    def test_loads_all_profiles_and_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        self._write(tmp_path, {
            "defaultProfile": "glm",
            "glm": self._profile(),
            "deep": self._profile(4000),
        })
        entries, default = _load_gateway_state()
        assert sorted(entries) == ["deep", "glm"]
        assert default == "glm"
        assert entries["deep"].profile.long_context_threshold == 4000
        assert "anthropic" in entries["glm"].providers
        assert "stepfun" in entries["glm"].providers["anthropic"]

    def test_single_profile_auto_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        self._write(tmp_path, {"glm": self._profile()})
        _, default = _load_gateway_state()
        assert default == "glm"

    def test_multiple_profiles_no_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        self._write(tmp_path, {"a": self._profile(), "b": self._profile()})
        _, default = _load_gateway_state()
        assert default is None

    def test_missing_default_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        self._write(tmp_path, {"defaultProfile": "ghost", "glm": self._profile()})
        with pytest.raises(SystemExit, match="ghost"):
            _load_gateway_state()


class TestGatewayHotReload:
    def _write(self, tmp_path, profiles, default=None):
        (tmp_path / "providers.json").write_text(json.dumps({"anthropic": {
            "stepfun": {"base_url": "https://x", "auth": "${K}"},
            "anthropic": {"base_url": "https://y", "auth": "${K}"},
        }}))
        routing = {name: {"protocol": "anthropic", "longContextThreshold": t,
                          "destinations": {"flash": "stepfun,sf", "pro": "anthropic,op"}}
                   for name, t in profiles.items()}
        if default:
            routing["defaultProfile"] = default
        (tmp_path / "routing.json").write_text(json.dumps(routing))

    def _app(self):
        # _reload_gateway only swaps these two entries; a plain dict is the app
        # (same as the single-profile TestHotReload tests)
        return {"gateway": {"old": _entry("old", "anthropic", "f", "p")},
                "default_profile": None}

    def test_reload_swaps_profiles_and_default(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        self._write(tmp_path, {"glm": 8000, "deep": 4000}, default="glm")
        app = self._app()
        assert _reload_gateway(app) is True
        assert sorted(app["gateway"]) == ["deep", "glm"]
        assert app["default_profile"] == "glm"
        assert app["gateway"]["deep"].profile.long_context_threshold == 4000
        assert "config reloaded -> 2 profile(s)" in capsys.readouterr().out

    def test_reload_announces_added_and_removed(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        self._write(tmp_path, {"glm": 8000})
        app = self._app()
        assert _reload_gateway(app) is True
        self._write(tmp_path, {"deep": 4000}, default="deep")
        assert _reload_gateway(app) is True
        out = capsys.readouterr().out
        assert "profiles added   -> deep" in out
        assert "profiles removed -> glm" in out
        assert app["default_profile"] == "deep"

    def test_invalid_config_keeps_serving_previous(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        self._write(tmp_path, {"glm": 8000})
        app = self._app()
        assert _reload_gateway(app) is True
        old = app["gateway"]
        (tmp_path / "routing.json").write_text("{ broken")
        assert _reload_gateway(app) is False
        assert app["gateway"] is old
        assert "reload skipped" in capsys.readouterr().out

    def test_invalid_awecompress_config_keeps_serving_previous(self, tmp_path, monkeypatch, capsys):
        from awerouter import server as server_mod

        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(server_mod, "Compressor", object)
        self._write(tmp_path, {"glm": 8000})
        app = self._app()
        assert _reload_gateway(app) is True
        old = app["gateway"]
        routing_path = tmp_path / "routing.json"
        routing = json.loads(routing_path.read_text())
        routing["glm"]["awecompress"] = {"summaryModel": "missing-model"}
        routing_path.write_text(json.dumps(routing))

        assert _reload_gateway(app) is False
        assert app["gateway"] is old
        assert "missing-model" in capsys.readouterr().out

    def test_empty_config_keeps_serving_previous(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        self._write(tmp_path, {"glm": 8000})
        app = self._app()
        assert _reload_gateway(app) is True
        old = app["gateway"]
        (tmp_path / "routing.json").write_text(json.dumps({"settings": {}}))
        assert _reload_gateway(app) is False
        assert app["gateway"] is old
        assert "no profiles" in capsys.readouterr().out


class TestDefaultProfileConfig:
    """routing.json's top-level defaultProfile key (config.py parsing)."""

    def _write(self, tmp_path, routing):
        (tmp_path / "providers.json").write_text(json.dumps({"anthropic": {
            "p": {"base_url": "https://x", "auth": "${K}"},
        }}))
        (tmp_path / "routing.json").write_text(json.dumps(routing))

    def _profile(self):
        return {"protocol": "anthropic", "longContextThreshold": 1,
                "destinations": {"flash": "p,m", "pro": "p,m"}}

    def test_parsed_onto_global_settings(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        self._write(tmp_path, {"defaultProfile": "cc-1", "cc-1": self._profile()})
        settings, profiles = load_routing()
        assert settings.default_profile == "cc-1"
        # per-profile copies do not carry it (it is not a routing knob)
        assert profiles["cc-1"].settings.default_profile is None

    def test_unknown_name_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        self._write(tmp_path, {"defaultProfile": "ghost", "cc-1": self._profile()})
        with pytest.raises(SystemExit, match="defaultProfile 'ghost'"):
            load_routing()

    def test_non_string_dies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        self._write(tmp_path, {"defaultProfile": 42, "cc-1": self._profile()})
        with pytest.raises(SystemExit, match="defaultProfile"):
            load_routing()

    def test_profile_body_with_key_dies_as_unknown(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        body = dict(self._profile(), defaultProfile="cc-1")
        self._write(tmp_path, {"cc-1": body})
        with pytest.raises(SystemExit, match="unknown key"):
            load_routing()

    def test_config_show_displays_it(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AWEROUTER_CONFIG_DIR", str(tmp_path))
        self._write(tmp_path, {"defaultProfile": "cc-1", "cc-1": self._profile()})
        validate_profiles(load_providers(), load_routing()[1])
        settings, profiles = load_routing()
        assert '"defaultProfile": "cc-1"' in format_routing_display(settings, profiles)


class TestGatewayClientHints:
    def test_with_default_includes_tier_env(self):
        entries = {"glm": _entry("glm", "anthropic", "f", "p")}
        hints = _gateway_client_hints(entries, "glm", "127.0.0.1", 20128)
        assert "ANTHROPIC_BASE_URL=http://127.0.0.1:20128" in hints
        assert "bare names route to 'glm'" in hints
        assert "glm/auto" in hints

    def test_without_default_omits_tier_env(self):
        entries = {
            "a": _entry("a", "anthropic", "f", "p"),
            "b": _entry("b", "openai-chat", "f", "p"),
        }
        hints = _gateway_client_hints(entries, None, "127.0.0.1", 20128)
        assert "ANTHROPIC_MODEL" not in hints  # bare names 400: no tier env lies
        assert "bare names are rejected" in hints


# ---------------------------------------------------------------------------
# Failover in gateway mode: aliases walk queues, direct forwards stay pinned
# ---------------------------------------------------------------------------

class TestGatewayFailover:
    @pytest.fixture(autouse=True)
    def _clear_cooldown(self):
        from awerouter import server
        server._COOLDOWN_UNTIL.clear()
        yield
        server._COOLDOWN_UNTIL.clear()

    def test_alias_walks_declared_backups(self):
        """'<profile>/auto' resolves through the smart pipeline and then walks
        the profile's failover queue, same as single-profile serve."""
        hits = []

        async def t():
            async def up(request):
                body = await request.json()
                hits.append(body["model"])
                if body["model"] == "glm-flash":
                    return web.json_response({"error": "quota"}, status=429)
                return web.json_response({"model": body["model"]})

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                profile = RoutingProfile(
                    "glm", "anthropic", 32,
                    {"flash": Destination("glm", "glm-flash"),
                     "pro": Destination("anthropic", "glm-pro")},
                    backups={"flash": [Destination("glm", "glm-air")],
                             "pro": [Destination("glm", "glm-max")]})
                entries = {"glm": _GatewayEntry(
                    profile=profile, settings=Settings(),
                    providers={p: _direct_providers(up_server.port)[p]
                               for p in ("anthropic",)})}
                async with TestClient(TestServer(create_gateway_app(entries, "glm"))) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "glm/auto", "messages": [{"content": "hi"}],
                    })
                    assert r.status == 200
                    assert (await r.json())["model"] == "glm-air"
            finally:
                await up_server.close()
        run(t())
        assert hits == ["glm-flash", "glm-air"]

    def test_direct_forward_never_fails_over(self):
        """A pinned 'provider/<model>' forward that 429s passes the response
        through — swapping the named model for another would not be what was asked for."""
        calls = []

        async def t():
            async def up(request):
                calls.append(1)
                return web.json_response({"error": "quota"}, status=429)

            up_app = web.Application()
            up_app.router.add_post("/v1/messages", up)
            up_server = TestServer(up_app)
            await up_server.start_server()
            try:
                entries = {"glm": _entry("glm", "anthropic", "glm-flash", "glm-pro")}
                async with TestClient(TestServer(
                        create_gateway_app(entries, "glm", _direct_providers(up_server.port)))) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "stepfun/step-3.7-flash",
                        "messages": [{"content": "hi"}],
                    })
                    assert r.status == 429
            finally:
                await up_server.close()
        run(t())
        assert len(calls) == 1  # exactly one attempt: the pinned destination


class TestGatewayDirectPool:
    """'provider/<model>' forwards fail over across same-pool accounts — the
    SAME model on the next account in declaration order wrapping from the
    named entry. One 'pool' tag per entry replaces the O(n^2) mutual-backups
    lists the multi-account quota-spread setup would otherwise need."""

    @pytest.fixture(autouse=True)
    def _clear_cooldowns(self):
        from awerouter import server
        server._COOLDOWN_UNTIL.clear()
        yield
        server._COOLDOWN_UNTIL.clear()

    def _pooled_providers(self, port, pool="stepfun",
                          models=("step-3.7-flash", "step-router-v1")):
        def group():
            return {
                f"stepfun-{i}": Provider(f"stepfun-{i}", f"http://127.0.0.1:{port}",
                                         f"${{STEPFUN_KEY_{i}}}", models=models,
                                         pool=pool)
                for i in (1, 2, 3)
            }
        return {p: group() for p in PROTOCOLS}

    def _quota_upstream(self, dead_keys, hits):
        """One mock upstream: accounts whose key is in dead_keys answer 429,
        everyone else echoes the model back."""
        async def up(request):
            auth = request.headers.get("authorization", "")
            hits.append(auth.removeprefix("Bearer "))
            if auth.removeprefix("Bearer ") in dead_keys:
                return web.json_response({"error": "quota"}, status=429)
            return web.json_response({"model": (await request.json())["model"]})
        app = web.Application()
        app.router.add_post("/v1/messages", up)
        return TestServer(app)

    def test_429_fails_over_to_next_account_same_model(self, monkeypatch):
        for i in (1, 2, 3):
            monkeypatch.setenv(f"STEPFUN_KEY_{i}", f"key-{i}")
        hits = []

        async def t():
            up_server = self._quota_upstream({"key-1"}, hits)
            await up_server.start_server()
            try:
                entries = {"glm": _entry("glm", "anthropic", "f", "p")}
                providers = self._pooled_providers(up_server.port)
                async with TestClient(TestServer(
                        create_gateway_app(entries, "glm", providers))) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "stepfun-1/step-3.7-flash",
                        "messages": [{"content": "hi"}],
                    })
                    assert r.status == 200
                    assert (await r.json())["model"] == "step-3.7-flash"
            finally:
                await up_server.close()
        run(t())
        assert hits == ["key-1", "key-2"]
        from awerouter.logging import tail
        e = tail(1)[0]
        assert e.profile == "direct"
        assert e.provider == "stepfun-2"
        assert e.label == "direct→fb:stepfun-2,step-3.7-flash"
        assert e.fallback_hops == 1

    def test_pool_wraps_from_the_named_entry(self, monkeypatch):
        """Naming the middle account hops to the LAST one next — declaration
        order wraps, overflow flows to the next account, not back to a
        de-facto primary."""
        for i in (1, 2, 3):
            monkeypatch.setenv(f"STEPFUN_KEY_{i}", f"key-{i}")
        hits = []

        async def t():
            up_server = self._quota_upstream({"key-2"}, hits)
            await up_server.start_server()
            try:
                entries = {"glm": _entry("glm", "anthropic", "f", "p")}
                providers = self._pooled_providers(up_server.port)
                async with TestClient(TestServer(
                        create_gateway_app(entries, "glm", providers))) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "stepfun-2/step-3.7-flash",
                        "messages": [{"content": "hi"}],
                    })
                    assert r.status == 200
            finally:
                await up_server.close()
        run(t())
        assert hits == ["key-2", "key-3"]

    def test_exhausted_pool_passes_the_last_response_through(self, monkeypatch):
        for i in (1, 2, 3):
            monkeypatch.setenv(f"STEPFUN_KEY_{i}", f"key-{i}")
        hits = []

        async def t():
            up_server = self._quota_upstream({"key-1", "key-2", "key-3"}, hits)
            await up_server.start_server()
            try:
                entries = {"glm": _entry("glm", "anthropic", "f", "p")}
                providers = self._pooled_providers(up_server.port)
                async with TestClient(TestServer(
                        create_gateway_app(entries, "glm", providers))) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "stepfun-1/step-3.7-flash",
                        "messages": [{"content": "hi"}],
                    })
                    assert r.status == 429
            finally:
                await up_server.close()
        run(t())
        assert hits == ["key-1", "key-2", "key-3"]

    def test_member_without_the_model_is_skipped(self, monkeypatch):
        """A pool member that did not declare the model is not a candidate —
        capability, not error."""
        for i in (1, 2, 3):
            monkeypatch.setenv(f"STEPFUN_KEY_{i}", f"key-{i}")
        hits = []

        async def t():
            up_server = self._quota_upstream({"key-1"}, hits)
            await up_server.start_server()
            try:
                entries = {"glm": _entry("glm", "anthropic", "f", "p")}
                providers = self._pooled_providers(up_server.port)
                providers["anthropic"]["stepfun-2"] = Provider(
                    "stepfun-2", f"http://127.0.0.1:{up_server.port}",
                    "${STEPFUN_KEY_2}", models=("step-router-v1",), pool="stepfun")
                async with TestClient(TestServer(
                        create_gateway_app(entries, "glm", providers))) as c:
                    r = await c.post("/v1/messages", json={
                        "model": "stepfun-1/step-3.7-flash",
                        "messages": [{"content": "hi"}],
                    })
                    assert r.status == 200
            finally:
                await up_server.close()
        run(t())
        assert hits == ["key-1", "key-3"]
