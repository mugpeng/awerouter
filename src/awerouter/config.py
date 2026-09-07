from __future__ import annotations

import copy
import difflib
import ipaddress
import json
import os
import re
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Optional

import click

from urllib.parse import urlparse

from awerouter import __version__
from awerouter.claude import AUTH_SENTINEL as CLAUDE_SENTINEL
from awerouter.codex import AUTH_SENTINEL
from awerouter.protocols import PROTOCOL_IDS
from awerouter.types import AutoThresholdConfig, AwecompressConfig, Destination, OdcpConfig, Provider, RoutingProfile, Settings, ToolRoutingConfig

# ---------------------------------------------------------------------------
# Constants (mirror aweswitch cli.py conventions exactly)
# ---------------------------------------------------------------------------

ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
SECRET_RE = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD|AUTH)", re.IGNORECASE)

# Implicit listen port: the first serve instance takes it, later ones scan up.
DEFAULT_PORT = 20128

# Bundled templates: <name>.providers.json + <name>.routing.json pairs.
TEMPLATE_DIR = Path(__file__).parent / "resources" / "templates"


def die(message: str) -> "SystemExit":
    raise SystemExit(f"awerouter: {message}")


def detect_auth_header(base_url: str) -> str:
    """Auto-detect auth header from base_url.

    anthropic.com endpoints use x-api-key (bare token); everyone else uses
    Authorization (Bearer prefix added at request time). Matched on netloc,
    not substring — "https://evil.com/anthropic.com" must not match.
    """
    netloc = urlparse(base_url).netloc.lower()
    is_anthropic = netloc == "api.anthropic.com" or netloc.endswith(".anthropic.com")
    return "x-api-key" if is_anthropic else "authorization"


def is_loopback_url(base_url: str) -> bool:
    """True when base_url points at this machine — local model servers that
    need no auth (Ollama, LM Studio, llama.cpp, vLLM on localhost).

    Parsed as an IP (whole 127/8 and ::1 are loopback) rather than prefix
    matching, so "127.0.0.1.evil.com" does not count as local.
    """
    netloc = urlparse(base_url).netloc.lower()
    if "@" in netloc:  # strip userinfo
        netloc = netloc.rsplit("@", 1)[1]
    if netloc.startswith("["):  # [::1]:port
        host = netloc[1:].split("]", 1)[0]
    else:
        host = netloc.rsplit(":", 1)[0] if netloc.rsplit(":", 1)[-1].isdigit() else netloc
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def config_dir() -> Path:
    return Path(os.environ.get("AWEROUTER_CONFIG_DIR", "~/.config/awerouter")).expanduser()


def providers_path() -> Path:
    return config_dir() / "providers.json"


def routing_path() -> Path:
    return config_dir() / "routing.json"


def backup_path(p: Path) -> Path:
    """Single-slot .bak sibling of a config file (aweswitch convention)."""
    return p.with_name(p.name + ".bak")


def backup_file(p: Path) -> "Path | None":
    """Snapshot p to its .bak before an awerouter-mediated write."""
    if not p.exists():
        return None
    bak = backup_path(p)
    shutil.copy2(p, bak)
    return bak


# ---------------------------------------------------------------------------
# Value helpers (mirror aweswitch exactly)
# ---------------------------------------------------------------------------

def expand_value(value, env: dict) -> "str | int | float | bool | None":
    if not isinstance(value, str):
        return value

    def replace(match):
        name = match.group(1)
        if name not in env:
            die(
                f"required environment variable not set: {name}\n"
                f"  Add it to your shell config (e.g. ~/.zshrc or ~/.bashrc), then reload your shell."
            )
        return env[name]

    return ENV_REF_RE.sub(replace, value)


def redact(data):
    redacted = json.loads(json.dumps(data))  # deep copy via JSON

    def walk(value, key=""):
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                if SECRET_RE.search(child_key) and isinstance(child_value, str):
                    value[child_key] = "<redacted>"
                else:
                    walk(child_value, child_key)
        elif isinstance(value, list):
            for item in value:
                walk(item, key)

    walk(redacted)
    return redacted


# ---------------------------------------------------------------------------
# Load / validate
# ---------------------------------------------------------------------------

def _load_json(path: Path, label: str) -> dict:
    if not path.exists():
        die(f"{label} not found: {path}\nrun: awerouter init")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        die(f"invalid JSON in {path}: {exc}")
    if not isinstance(data, dict):
        die(f"{label} must be a JSON object: {path}")
    return data


def _parse_destination(raw: str) -> Destination:
    parts = raw.split(",", 1)
    if len(parts) != 2:
        die(f"destination must be 'provider,model': {raw}")
    provider_name, model = parts[0].strip(), parts[1].strip()
    if not provider_name or not model:
        die(f"destination must be 'provider,model': {raw}")
    return Destination(provider_name=provider_name, model=model)


_OLD_AGENT_GROUPS = {"claude": "anthropic", "codex": "openai-chat / openai-responses"}


def _die_bad_protocol_group(key: str) -> "SystemExit":
    if key in _OLD_AGENT_GROUPS:
        return die(
            f"providers.json group '{key}' uses the old agent names — rename: "
            + ", ".join(f"'{k}' → {v}" for k, v in _OLD_AGENT_GROUPS.items())
        )
    return die(
        f"providers.json group '{key}' must be a protocol id: "
        f"{', '.join(PROTOCOL_IDS)}"
    )


def load_providers(path: Optional[Path] = None) -> dict[str, dict[str, Provider]]:
    """Load providers grouped by protocol. Returns {protocol: {provider_name: Provider}}."""
    path = path or providers_path()
    data = _load_json(path, "providers.json")
    result: dict[str, dict[str, Provider]] = {}
    for protocol, group in data.items():
        if protocol not in PROTOCOL_IDS:
            _die_bad_protocol_group(protocol)
        if not isinstance(group, dict):
            die(f"protocol group '{protocol}' must be an object")
        group_providers: dict[str, Provider] = {}
        for name, entry in group.items():
            if not isinstance(entry, dict):
                die(f"provider '{protocol}.{name}' must be an object")
            base_url = entry.get("base_url")
            # auth absent / null / "" = no-auth upstream (local model servers).
            auth = entry.get("auth") or None
            if auth == AUTH_SENTINEL and protocol != "openai-responses":
                die(
                    f"provider '{protocol}.{name}': auth '{AUTH_SENTINEL}' (local Codex CLI "
                    "login) belongs in the openai-responses group — the ChatGPT Codex "
                    "backend speaks the Responses protocol"
                )
            if auth == CLAUDE_SENTINEL and protocol != "anthropic":
                die(
                    f"provider '{protocol}.{name}': auth '{CLAUDE_SENTINEL}' (Claude "
                    "subscription OAuth login) belongs in the anthropic group — the "
                    "subscription backend speaks the Messages protocol"
                )
            if not base_url:
                die(f"provider '{protocol}.{name}' missing base_url")
            auth_header = entry.get("auth_header") or detect_auth_header(base_url)
            models = _parse_provider_models(protocol, name, entry.get("models"))
            multimodal_raw = entry.get("multimodal", False)
            if not isinstance(multimodal_raw, bool):
                die(f"provider '{protocol}.{name}' 'multimodal' must be true or "
                    f"false, got: {multimodal_raw!r}")
            pool_raw = entry.get("pool")
            if pool_raw is None:
                pool = ""
            elif isinstance(pool_raw, str) and pool_raw.strip():
                pool = pool_raw.strip()
            else:
                die(f"provider '{protocol}.{name}' 'pool' must be a non-empty "
                    f"account-pool id string (shared by same-vendor entries), "
                    f"got: {pool_raw!r}")
            group_providers[name] = Provider(
                name=name, base_url=base_url, auth=auth, auth_header=auth_header,
                models=models, multimodal=multimodal_raw, pool=pool,
            )
        result[protocol] = group_providers
    return result


def _parse_provider_models(protocol: str, name: str, raw) -> tuple:
    """Parse a provider's optional 'models' list — the models it can directly
    serve under gateway mode ('provider/<model>' names). Absent = empty tuple.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        die(f"provider '{protocol}.{name}' 'models' must be a list of model ids")
    models = []
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            die(f"provider '{protocol}.{name}' 'models' entries must be non-empty "
                f"model-id strings, got: {entry!r}")
        models.append(entry.strip())
    return tuple(models)


def _parse_backups(name: str, raw, destinations: dict) -> dict:
    """Parse a profile's optional 'backups' block: ordered failover queues
    keyed by destination key, each entry a 'provider,model' destination
    (a bare string is a one-element queue). Empty dict = zero-config.

    Explicit lists replace the implicit cross-tier hop for that tier — the
    queue is exactly what was written — so a duplicate of the tier's primary,
    or a duplicate inside the list, is a config error, not a silent dead hop.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        die(f"profile '{name}': 'backups' must be an object keyed by flash/pro")
    backups: dict[str, list] = {}
    for tier, entries in raw.items():
        if tier not in ("flash", "pro"):
            die(f"profile '{name}' backups key must be flash or pro, got: {tier}")
        if isinstance(entries, str):
            entries = [entries]
        if (not isinstance(entries, list) or not entries
                or not all(isinstance(e, str) for e in entries)):
            die(f"profile '{name}' backups '{tier}' must be a 'provider,model' "
                f"string or a non-empty list of them")
        queue: list = []
        seen = {f"{destinations[tier].provider_name},{destinations[tier].model}"}
        for entry in entries:
            dest = _parse_destination(entry)
            key = f"{dest.provider_name},{dest.model}"
            if key in seen:
                die(f"profile '{name}' backups '{tier}' repeats '{key}' (or it "
                    "duplicates the primary — a dead hop in the queue)")
            seen.add(key)
            queue.append(dest)
        backups[tier] = queue
    return backups


def _parse_auto_threshold(raw, base: AutoThresholdConfig | None = None,
                          where: str = "routing.json settings") -> AutoThresholdConfig:
    """Parse a longContextAuto block (all fields optional; missing fields
    inherit from base when given, else the type defaults)."""
    cfg = replace(base) if base is not None else AutoThresholdConfig()
    if raw is None:
        return cfg
    if not isinstance(raw, dict):
        die(f"{where} 'longContextAuto' must be an object")

    def int_field(key: str, lo: int, hi: int | None = None) -> "int | None":
        if key not in raw:
            return None
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, int):
            die(f"{where} longContextAuto '{key}' must be an integer, got: {value!r}")
        bound = f" in {lo}-{hi}" if hi is not None else f" >= {lo}"
        if value < lo or (hi is not None and value > hi):
            die(f"{where} longContextAuto '{key}' must be{bound}, got: {value}")
        return value

    if (v := int_field("percentile", 1, 99)) is not None:
        cfg.percentile = v
    if (v := int_field("windowDays", 1)) is not None:
        cfg.window_days = v
    if (v := int_field("minSamples", 1)) is not None:
        cfg.min_samples = v
    if (v := int_field("fallbackThreshold", 0)) is not None:
        cfg.fallback_threshold = v
    return cfg


def _parse_tool_routing(raw, base: ToolRoutingConfig | None = None,
                        where: str = "routing.json settings") -> ToolRoutingConfig:
    """A toolRouting block: one place for every tool-keyed routing rule:
    {"webSearch": "pro", "edit": "pro"}.

    Values are destination keys or null; an absent block = base (or defaults)
    — webSearch null falls back to the legacy webSearchModel setting."""
    cfg = replace(base) if base is not None else ToolRoutingConfig()
    if raw is None:
        return cfg
    if not isinstance(raw, dict):
        die(f"{where} 'toolRouting' must be an object")
    for removed in ("search", "mechanical"):
        if removed in raw:
            die(
                f"{where} toolRouting '{removed}' was removed: L4 is now a "
                f"single edit checkpoint (search/mechanical routed to flash, which is "
                f"already the default); delete the key — see CHANGELOG v0.4.8"
            )
    keys = {"webSearch": "web_search", "edit": "edit"}
    for json_key, field_name in keys.items():
        value = raw.get(json_key, getattr(cfg, field_name))
        if value is not None and value not in ("flash", "pro"):
            die(f"{where} toolRouting '{json_key}' must be "
                f"'flash', 'pro', or null, got: {value!r}")
        setattr(cfg, field_name, value)
    return cfg


# Every settings key, global or per-profile — a typo'd key dies at load
# instead of silently inheriting the global value.
_SETTINGS_KEYS = frozenset({
    "backgroundModel", "thinkModel", "webSearchModel", "imageModel",
    "defaultModel", "searchResultDiscount", "imageBridge", "toolRouting",
    "longContextAuto",
})

# Keys that belong to the profile body itself (settings keys may join them).
_PROFILE_KEYS = frozenset({
    "protocol", "longContextThreshold", "destinations", "port", "rtk", "odcp",
    "awecompress", "backups",
})


def _model_field(raw: dict, key: str, inherited: str, where: str, hint: str = "") -> str:
    """A flash/pro destination key with validation ('imageModel' and friends)."""
    value = str(raw.get(key, inherited))
    if value not in ("flash", "pro"):
        die(f"{where} '{key}' must be 'flash' or 'pro'{hint}, got: {value!r}")
    return value


def _parse_odcp(name: str, raw) -> "OdcpConfig | None":
    """'odcp': true enables both strategies with defaults; an object tunes
    them ({"dedup": false, "purgeErrors": {"turns": 4}}). Absent/false = off,
    the transparent default (same convention as rtk)."""
    if raw is False or raw is None:
        return None
    if raw is True:
        return OdcpConfig()
    if not isinstance(raw, dict):
        die(f"profile '{name}': 'odcp' must be true, false, or an object, got: {raw!r}")
    unknown = set(raw) - {"dedup", "purgeErrors"}
    if unknown:
        die(f"profile '{name}': unknown odcp key(s): {', '.join(sorted(unknown))}; "
            "expected 'dedup' and/or 'purgeErrors'")
    dedup = raw.get("dedup", True)
    if not isinstance(dedup, bool):
        die(f"profile '{name}': odcp 'dedup' must be true or false, got: {dedup!r}")
    purge = raw.get("purgeErrors", True)
    turns = 4
    if isinstance(purge, dict):
        unknown = set(purge) - {"turns"}
        if unknown:
            die(f"profile '{name}': unknown odcp purgeErrors key(s): "
                f"{', '.join(sorted(unknown))}; expected 'turns'")
        turns = purge.get("turns", 4)
        purge = True
    if not isinstance(purge, bool):
        die(f"profile '{name}': odcp 'purgeErrors' must be true, false, or an object, "
            f"got: {purge!r}")
    if isinstance(turns, bool) or not isinstance(turns, int) or turns < 1:
        die(f"profile '{name}': odcp purgeErrors 'turns' must be an integer >= 1, got: {turns!r}")
    return OdcpConfig(dedup=dedup, purge_errors=purge, purge_turns=turns)


_AWECOMPRESS_INT_KEYS = {
    "thresholdTokens": ("threshold_tokens", 1000),
    "keepRecentTurns": ("keep_recent_turns", 1),
    "minSpanTokens": ("min_span_tokens", 1000),
    "transcriptResultCap": ("transcript_result_cap", 100),
}
_AWECOMPRESS_LIST_KEYS = ("protectedTools", "protectedFilePatterns")


def _parse_awecompress(name: str, raw) -> "AwecompressConfig | None":
    """'awecompress': true enables frozen-summary compression with defaults;
    an object tunes it. Absent/false = off, the transparent default (same
    convention as rtk/odcp)."""
    if raw is False or raw is None:
        return None
    if raw is True:
        return AwecompressConfig()
    if not isinstance(raw, dict):
        die(f"profile '{name}': 'awecompress' must be true, false, or an object, got: {raw!r}")
    known = set(_AWECOMPRESS_INT_KEYS) | set(_AWECOMPRESS_LIST_KEYS) | {"summaryModel"}
    unknown = set(raw) - known
    if unknown:
        die(f"profile '{name}': unknown awecompress key(s): {', '.join(sorted(unknown))}; "
            f"expected any of: {', '.join(sorted(known))}")
    cfg = AwecompressConfig()
    for key, value in raw.items():
        if key == "summaryModel":
            if not isinstance(value, str):
                die(f"profile '{name}': awecompress 'summaryModel' must be a string, "
                    f"got: {value!r}")
            cfg.summary_model = value
        elif key in _AWECOMPRESS_INT_KEYS:
            field, minimum = _AWECOMPRESS_INT_KEYS[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                die(f"profile '{name}': awecompress '{key}' must be an integer "
                    f">= {minimum}, got: {value!r}")
            setattr(cfg, field, value)
        else:  # list key
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                die(f"profile '{name}': awecompress '{key}' must be an array of strings")
            setattr(cfg, "protected_tools" if key == "protectedTools"
                    else "protected_file_patterns", tuple(value))
    return cfg


def _parse_settings(raw: dict, base: Settings | None = None,
                    where: str = "routing.json settings") -> Settings:
    """Parse a settings block over a base: global settings parse with no base
    (type defaults apply), a profile's block parses over the global one —
    every missing key inherits, nested blocks (toolRouting, longContextAuto)
    inherit field by field."""
    if not isinstance(raw, dict):
        die(f"{where} must be an object")
    unknown = set(raw) - _SETTINGS_KEYS
    if unknown:
        die(f"{where}: unknown key(s): {', '.join(sorted(unknown))}")
    b = base if base is not None else Settings()

    raw_discount = raw.get("searchResultDiscount", b.search_result_discount)
    try:
        discount = float(raw_discount)
    except (TypeError, ValueError):
        die(f"{where} 'searchResultDiscount' must be a number in [0, 1], got: {raw_discount!r}")
    if not 0 <= discount <= 1:
        die(f"{where} 'searchResultDiscount' must be in [0, 1], got: {discount}")

    raw_bridge = raw.get("imageBridge", b.image_bridge)
    if not isinstance(raw_bridge, bool):
        die(f"{where} 'imageBridge' must be true or false, got: {raw_bridge!r}")

    return Settings(
        background_model=str(raw.get("backgroundModel", b.background_model)),
        think_model=str(raw.get("thinkModel", b.think_model)),
        web_search_model=_model_field(raw, "webSearchModel", b.web_search_model, where,
                                      hint=" (or move it into toolRouting.webSearch)"),
        image_model=_model_field(raw, "imageModel", b.image_model, where),
        default_model=_model_field(raw, "defaultModel", b.default_model, where),
        search_result_discount=discount,
        image_bridge=raw_bridge,
        long_context_auto=_parse_auto_threshold(
            raw.get("longContextAuto"), b.long_context_auto, where),
        tool_routing=_parse_tool_routing(raw.get("toolRouting"), b.tool_routing, where),
    )


def load_routing(path: Optional[Path] = None) -> tuple[Settings, dict[str, RoutingProfile]]:
    """Load global settings + all routing profiles keyed by profile id."""
    path = path or routing_path()
    data = _load_json(path, "routing.json")

    # Optional global settings (defaults: flash/pro) — the base every
    # profile-level settings block inherits from.
    raw_settings = data.pop("settings", {})
    # Reserved top-level key (like "settings"): the gateway's bare-name
    # default. Sits outside the settings block because it cannot be
    # overridden per profile.
    default_profile = data.pop("defaultProfile", None)
    if default_profile is not None and (not isinstance(default_profile, str) or not default_profile):
        die(f"routing.json 'defaultProfile' must be a profile name, got: {default_profile!r}")
    settings = _parse_settings(raw_settings)

    profiles: dict[str, RoutingProfile] = {}
    for name, body in data.items():
        if not isinstance(body, dict):
            die(f"profile '{name}' must be an object")
        if "agent" in body:
            die(
                f"profile '{name}': 'agent' was renamed to 'protocol' "
                "(claude → anthropic, codex → openai-responses); edit routing.json"
            )
        # Settings keys may sit directly in the profile body (flat, next to
        # protocol/destinations); anything else is a typo and dies.
        unknown = set(body) - _PROFILE_KEYS - _SETTINGS_KEYS
        if unknown:
            listed = ", ".join(f"'{k}'" for k in sorted(unknown))
            die(
                f"profile '{name}': unknown key(s): {listed}; expected "
                f"profile keys ({', '.join(sorted(_PROFILE_KEYS))}) or settings keys "
                f"overriding the global block ({', '.join(sorted(_SETTINGS_KEYS))})"
            )
        protocol = body.get("protocol")
        if isinstance(protocol, str):
            protocols = [protocol]
        elif (isinstance(protocol, list) and protocol
              and all(isinstance(p, str) for p in protocol)):
            protocols = protocol
        else:
            die(
                f"profile '{name}': 'protocol' must be a protocol id or a non-empty "
                f"list of them; expected one or more of: {', '.join(PROTOCOL_IDS)}"
            )
        for p in protocols:
            if p not in PROTOCOL_IDS:
                die(
                    f"profile '{name}': unknown protocol '{p}'; "
                    f"expected one of: {', '.join(PROTOCOL_IDS)}"
                )
        if len(set(protocols)) != len(protocols):
            die(f"profile '{name}': 'protocol' lists a protocol more than once")
        for key in ("longContextThreshold", "destinations"):
            if key not in body:
                die(f"profile '{name}' missing required key: {key}")
        port_raw = body.get("port")
        if port_raw is not None:
            if isinstance(port_raw, bool) or not isinstance(port_raw, int) or not 1 <= port_raw <= 65535:
                die(f"profile '{name}': 'port' must be an integer in 1-65535, got: {port_raw!r}")
        rtk_raw = body.get("rtk", False)
        if not isinstance(rtk_raw, bool):
            die(f"profile '{name}': 'rtk' must be true or false, got: {rtk_raw!r}")
        odcp = _parse_odcp(name, body.get("odcp", False))
        awecompress_cfg = _parse_awecompress(name, body.get("awecompress", False))
        # Per-profile settings override the global block key by key (nested
        # blocks field by field); keys absent from the body inherit everything.
        raw_psettings = {k: v for k, v in body.items() if k in _SETTINGS_KEYS}
        psettings = _parse_settings(
            raw_psettings, base=settings, where=f"profile '{name}':")
        dests_raw = body["destinations"]
        if not isinstance(dests_raw, dict):
            die(f"profile '{name}' destinations must be an object")
        parsed: dict[str, Destination] = {}
        for tier, raw in dests_raw.items():
            if tier not in ("flash", "pro"):
                die(f"profile '{name}' destination key must be flash or pro, got: {tier}")
            parsed[tier] = _parse_destination(str(raw))
        for tier in ("flash", "pro"):
            # Both tiers must exist: resolve() and the implicit failover hop
            # index destinations by tier key, and backups for a missing tier
            # have no primary to back up — a load-time die() beats a
            # request-time KeyError.
            if tier not in parsed:
                die(f"profile '{name}' destinations must define both 'flash' and "
                    f"'pro' (missing '{tier}')")
        backups = _parse_backups(name, body.get("backups"), parsed)
        raw_threshold = body["longContextThreshold"]
        if raw_threshold == "auto":
            # Resolved at serve start; the fallback value keeps reads before
            # that (config show, tests) meaningful. A profile may retune it
            # via its own longContextAuto.fallbackThreshold.
            threshold_auto = True
            threshold = psettings.long_context_auto.fallback_threshold
        else:
            threshold_auto = False
            try:
                threshold = int(raw_threshold)
            except (TypeError, ValueError):
                die(f"profile '{name}': 'longContextThreshold' must be a non-negative "
                    f"integer or \"auto\", got: {raw_threshold!r}")
            if threshold < 0:
                die(f"profile '{name}': 'longContextThreshold' must be a non-negative "
                    f"integer or \"auto\", got: {threshold}")
        profiles[name] = RoutingProfile(
            name=name,
            protocols=protocols,
            long_context_threshold=threshold,
            destinations=parsed,
            port=port_raw,
            threshold_auto=threshold_auto,
            rtk=rtk_raw,
            odcp=odcp,
            awecompress=awecompress_cfg,
            settings=psettings,
            settings_overrides=raw_psettings,
            backups=backups,
        )
    if default_profile is not None and default_profile not in profiles:
        avail = ", ".join(profiles) or "(none)"
        die(f"defaultProfile '{default_profile}' not found in routing.json; available: {avail}")
    settings.default_profile = default_profile
    return settings, profiles


def validate_profiles(providers_all: dict, profiles: dict) -> None:
    """Cross-check every profile's protocols and destinations against providers.json.

    Called by both serve and config show, so bad references fail at load time
    instead of on the first request. A multi-protocol profile must resolve its
    destinations in every served group — each protocol carries its own
    provider entries (per-protocol base_urls), so a name present in one group
    but absent in another is a config error, not a runtime fallback.
    """
    for profile in profiles.values():
        for protocol in profile.protocols:
            group = providers_all.get(protocol)
            if group is None:
                avail = ", ".join(providers_all) or "(none)"
                die(
                    f"protocol '{protocol}' (for profile '{profile.name}') not found in "
                    f"providers.json; available: {avail}"
                )
            for tier, dest in profile.destinations.items():
                if dest.provider_name not in group:
                    avail = ", ".join(group) or "(none)"
                    die(
                        f"provider '{dest.provider_name}' (destination '{tier}' of profile "
                        f"'{profile.name}') not found in the '{protocol}' group of "
                        f"providers.json; available: {avail}"
                    )
            # Backup queues live or die with the same rule as destinations:
            # every candidate must resolve in every served group, so failover
            # can never hand a request to a provider the protocol can't reach.
            for tier, queue in profile.backups.items():
                for dest in queue:
                    if dest.provider_name not in group:
                        avail = ", ".join(group) or "(none)"
                        die(
                            f"provider '{dest.provider_name}' (backups '{tier}' of profile "
                            f"'{profile.name}') not found in the '{protocol}' group of "
                            f"providers.json; available: {avail}"
                        )


def load_for_profile(name: str) -> tuple[dict[str, dict[str, Provider]], RoutingProfile, Settings]:
    """Resolve one profile: returns (providers by served protocol, profile,
    the profile's effective settings — global merged with its overrides)."""
    providers_all = load_providers()
    settings, profiles = load_routing()
    if name not in profiles:
        avail = ", ".join(profiles) or "(none)"
        die(f"profile '{name}' not found in routing.json; available: {avail}")
    profile = profiles[name]
    validate_profiles(providers_all, {name: profile})
    return {p: providers_all[p] for p in profile.protocols}, profile, profile.settings


def load_default_profile() -> tuple[dict[str, Provider], RoutingProfile, Settings]:
    """Auto-select when only one profile exists; prompt otherwise."""
    settings, profiles = load_routing()
    if not profiles:
        die("no profiles in routing.json")
    if len(profiles) == 1:
        return load_for_profile(next(iter(profiles)))
    die(
        "multiple profiles available, specify one:\n"
        "  awerouter serve run <name>   (or `awerouter serve all` — every profile on one port)\n"
        f"available: {', '.join(profiles)}"
    )


# ---------------------------------------------------------------------------
# Init / template
# ---------------------------------------------------------------------------

def available_templates() -> list[str]:
    """Names of bundled template pairs (<name>.providers.json + <name>.routing.json)."""
    names = []
    for p in TEMPLATE_DIR.glob("*.providers.json"):
        name = p.name[: -len(".providers.json")]
        if (TEMPLATE_DIR / f"{name}.routing.json").exists():
            names.append(name)
    return sorted(names)


def _template_paths(template: str) -> tuple[Path, Path]:
    src_providers = TEMPLATE_DIR / f"{template}.providers.json"
    src_routing = TEMPLATE_DIR / f"{template}.routing.json"
    if not src_providers.exists() or not src_routing.exists():
        avail = ", ".join(available_templates()) or "(none)"
        die(f"unknown template '{template}'; available: {avail}")
    return src_providers, src_routing


def init_config(template: str = "default") -> None:
    d = config_dir()
    if providers_path().exists() or routing_path().exists():
        die(f"config already exists in {d}")
    d.mkdir(parents=True, exist_ok=True)
    src_providers, src_routing = _template_paths(template)
    shutil.copy2(src_providers, providers_path())
    shutil.copy2(src_routing, routing_path())


# Settings keys whose value re-routes every profile when it first appears:
# the image guard (default pro), the fall-through destination (default flash),
# and the image bridge (default off).
_BEHAVIOR_SETTINGS_DEFAULTS = {"imageModel": "pro", "defaultModel": "flash",
                               "imageBridge": False}


def merge_config(template: str = "default") -> dict:
    """Merge a bundled template into an existing config.

    Fill-missing everywhere: provider entries and profiles are added only when
    their key is absent, settings keys only when unset — an existing entry is
    never overwritten. Returns a report of what was added/skipped; keys that
    shift routing for all existing profiles are flagged in behavior_shift.
    """
    src_providers, src_routing = _template_paths(template)
    t_providers = json.loads(src_providers.read_text(encoding="utf-8"))
    t_routing = json.loads(src_routing.read_text(encoding="utf-8"))

    report = {
        "providers_added": [], "providers_skipped": [],
        "profiles_added": [], "profiles_skipped": [],
        "settings_added": [], "behavior_shift": [],
    }

    # Parse and merge both files in memory first, so a config error dies
    # before anything is written — no half-applied merges.
    p_path = providers_path()
    p_data = _load_json(p_path, "providers.json")
    p_changed = False
    for protocol, group in t_providers.items():
        existing = p_data.setdefault(protocol, {})
        for name, entry in group.items():
            if name in existing:
                report["providers_skipped"].append(f"{protocol}.{name}")
            else:
                existing[name] = entry
                report["providers_added"].append(f"{protocol}.{name}")
                p_changed = True

    r_path = routing_path()
    r_data = _load_json(r_path, "routing.json")
    r_changed = False
    settings = r_data.setdefault("settings", {})
    if not isinstance(settings, dict):
        die("routing.json 'settings' must be an object")
    for key, value in t_routing.get("settings", {}).items():
        if key in settings:
            continue
        settings[key] = value
        report["settings_added"].append(key)
        if _BEHAVIOR_SETTINGS_DEFAULTS.get(key, value) != value:
            report["behavior_shift"].append(f"{key}={value}")
        r_changed = True
    for name, body in t_routing.items():
        if name in ("settings", "defaultProfile"):
            continue  # reserved keys, not profiles (the default is never merged)
        if name in r_data:
            report["profiles_skipped"].append(name)
        else:
            r_data[name] = body
            report["profiles_added"].append(name)
            r_changed = True

    if p_changed:
        backup_file(p_path)
        p_path.write_text(json.dumps(p_data, indent=2) + "\n")
    if r_changed:
        backup_file(r_path)
        r_path.write_text(json.dumps(r_data, indent=2) + "\n")

    # Fail loudly rather than leave behind a config that serve rejects.
    validate_profiles(load_providers(), load_routing()[1])
    return report


def save_provider(protocol: str, name: str, base_url: str, auth: "str | None") -> None:
    """Append one provider entry to providers.json (auth None = local, key omitted)."""
    path = providers_path()
    data = _load_json(path, "providers.json")
    group = data.setdefault(protocol, {})
    if name in group:
        die(f"provider already exists: {protocol}.{name}")
    entry = {"base_url": base_url}
    if auth:
        entry["auth"] = auth
    group[name] = entry
    backup_file(path)
    path.write_text(json.dumps(data, indent=2) + "\n")


def save_profile_entry(
    name: str, protocol: str, long_context_threshold: "int | str", flash: str, pro: str
) -> None:
    """Append one profile entry to routing.json. flash/pro are 'provider,model';
    long_context_threshold may be the string "auto"."""
    path = routing_path()
    data = _load_json(path, "routing.json")
    if name in data:
        die(f"profile already exists: {name}")
    data[name] = {
        "protocol": protocol,
        "longContextThreshold": long_context_threshold,
        "destinations": {"flash": flash, "pro": pro},
    }
    backup_file(path)
    path.write_text(json.dumps(data, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Config display
# ---------------------------------------------------------------------------

def format_providers_display(all_providers: dict[str, dict[str, Provider]]) -> str:
    display = {}
    for protocol, group in all_providers.items():
        protocol_display = {}
        for name, p in group.items():
            entry = {"base_url": p.base_url, "auth_header": p.auth_header}
            if p.pool:
                entry["pool"] = p.pool
            if p.multimodal:
                entry["multimodal"] = True
            if not p.auth:
                entry["auth"] = None  # no-auth upstream (local model server)
            elif p.auth == AUTH_SENTINEL:
                entry["auth"] = "codex (local CLI login)"
            elif p.auth == CLAUDE_SENTINEL:
                entry["auth"] = "claude (subscription OAuth login)"
            elif ENV_REF_RE.fullmatch(str(p.auth)):
                entry["auth"] = str(p.auth)
            else:
                entry["auth"] = "<set>"
            protocol_display[name] = entry
        display[protocol] = protocol_display
    return json.dumps(display, indent=2)


def format_routing_display(settings: Settings, profiles: dict[str, RoutingProfile]) -> str:
    display = {
        # global-only gateway key: top-level in routing.json, never inherited
        # by profile Settings copies.
        **({"defaultProfile": settings.default_profile}
           if settings.default_profile else {}),
        "settings": {
            "backgroundModel": settings.background_model,
            "thinkModel": settings.think_model,
            "webSearchModel": settings.web_search_model,
            "imageModel": settings.image_model,
            "defaultModel": settings.default_model,
            "searchResultDiscount": settings.search_result_discount,
            "imageBridge": settings.image_bridge,
            "toolRouting": {
                "webSearch": settings.tool_routing.web_search or settings.web_search_model,
                "edit": settings.tool_routing.edit,
            },
            "longContextAuto": {
                "percentile": settings.long_context_auto.percentile,
                "windowDays": settings.long_context_auto.window_days,
                "minSamples": settings.long_context_auto.min_samples,
                "fallbackThreshold": settings.long_context_auto.fallback_threshold,
            },
        },
    }
    for name, p in profiles.items():
        entry = {
            # mirrors the config shape: a bare id for one protocol, a list for several
            "protocol": p.protocol if len(p.protocols) == 1 else list(p.protocols),
            "longContextThreshold": "auto" if p.threshold_auto else p.long_context_threshold,
        }
        if p.port is not None:
            entry["port"] = p.port
        if p.rtk:
            entry["rtk"] = True
        if p.odcp is not None:
            entry["odcp"] = {
                "dedup": p.odcp.dedup,
                "purgeErrors": {"turns": p.odcp.purge_turns} if p.odcp.purge_errors else False,
            }
        if p.awecompress is not None:
            entry["awecompress"] = {
                "summaryModel": p.awecompress.summary_model,
                "thresholdTokens": p.awecompress.threshold_tokens,
                "keepRecentTurns": p.awecompress.keep_recent_turns,
                "minSpanTokens": p.awecompress.min_span_tokens,
                "protectedTools": list(p.awecompress.protected_tools),
                "protectedFilePatterns": list(p.awecompress.protected_file_patterns),
            }
        # mirrors the config shape: overridden settings keys sit flat in the
        # profile body, next to protocol/destinations
        entry.update(p.settings_overrides)
        entry["destinations"] = {
            k: f"{v.provider_name},{v.model}" for k, v in p.destinations.items()
        }
        if p.backups:
            entry["backups"] = {
                k: [f"{d.provider_name},{d.model}" for d in queue]
                for k, queue in p.backups.items()
            }
        display[name] = entry
    return json.dumps(display, indent=2)


# ---------------------------------------------------------------------------
# Click CLI
# ---------------------------------------------------------------------------

def _close_command(group: click.Group, ctx, cmd_name: str) -> "str | None":
    """Closest real command name for a typo'd token, or None."""
    names = [c for c in group.list_commands(ctx) if not c.startswith("__")]
    close = difflib.get_close_matches(cmd_name, names, n=1, cutoff=0.8)
    return close[0] if close else None


class SuggestGroup(click.Group):
    """Group that turns unknown subcommands into friendly errors:
    a close match gets a did-you-mean suggestion, anything else gets a
    pointer to `-h` — never a bare usage error."""

    def resolve_command(self, ctx, args):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            if not args:
                raise
            cmd_name = args[0]
            close = _close_command(self, ctx, cmd_name)
            if close:
                ctx.fail(
                    f"unknown command '{cmd_name}' — did you mean '{close}'? "
                    f"(run {ctx.command_path} -h for usage)"
                )
            ctx.fail(
                f"unknown command '{cmd_name}' — "
                f"run {ctx.command_path} -h to list commands"
            )


class ProfileGroup(SuggestGroup):
    """Group where an unknown subcommand is treated as a profile name:
    `awerouter cc-router-1` == `awerouter serve run cc-router-1`.

    Defined commands always win, so profiles named after commands are
    unreachable via the shorthand (use `serve run <name>` for those). A token
    that closely resembles a real command (e.g. `server` vs `serve`) is
    reported as a typo instead of being taken as a profile name; a bare
    unknown token with extra positional arguments can't be a profile launch
    either, so it gets the -h pointer.
    """

    # Where a typo'd token should point users to launch a profile instead.
    profile_hint = "awerouter serve run <profile> or awerouter <profile>"

    def resolve_command(self, ctx, args):
        try:
            # Bypass SuggestGroup: unknown tokens may be profile names.
            return click.Group.resolve_command(self, ctx, args)
        except click.UsageError:
            if not args:
                raise
            cmd_name = args[0]
            close = _close_command(self, ctx, cmd_name)
            if close:
                ctx.fail(
                    f"unknown command '{cmd_name}' — did you mean '{close}'? "
                    f"(to start a profile: {self.profile_hint})"
                )
            if self._has_stray_positionals(args[1:]):
                ctx.fail(
                    f"unknown command '{cmd_name}' — run {ctx.command_path} -h to list commands"
                )
            ctx.meta["profile_name"] = cmd_name
            command = self.get_command(ctx, "__serve_profile__")
            return args[0], command, args[1:]

    @staticmethod
    def _has_stray_positionals(rest: list) -> bool:
        """True when any arg is a positional, not an option or an option value.

        The bare-profile launch only accepts --port/--host, so leftover
        positionals mean this can't be a valid profile invocation.
        """
        expect_value = False
        for a in rest:
            if expect_value:
                expect_value = False
                continue
            if a in ("--port", "--host"):
                expect_value = True
                continue
            if a.startswith("-"):
                continue
            return True
        return False


@click.group(
    cls=ProfileGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(__version__, "-v", "--version", message="awerouter %(version)s")
def cli():
    """Smart LLM router: fast cheap tasks to flash, hard decisions to pro."""


@cli.group(cls=SuggestGroup, context_settings={"help_option_names": ["-h", "--help"]})
def config():
    """Manage awerouter config."""


@config.command("path")
def config_path_cmd():
    """Print both config file paths."""
    click.echo(providers_path())
    click.echo(routing_path())


@config.command("show")
@click.argument("profile", required=False)
def config_show_cmd(profile):
    """Show config, secrets redacted; with PROFILE, only its view."""
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
    used = {}
    for proto in p.protocols:
        group = providers_all[proto]
        # Backup-referenced providers belong to the profile's view too:
        # failover behavior (and the image guard) depends on their flags.
        dests = list(p.destinations.values())
        for queue in p.backups.values():
            dests += queue
        used[proto] = {d.provider_name: group[d.provider_name] for d in dests}
    click.echo("providers:")
    click.echo(format_providers_display(used))
    click.echo()
    click.echo("profile:")
    # the settings block shows this profile's effective values (global merged
    # with its overrides); the profile entry lists the override keys only
    click.echo(format_routing_display(p.settings, {profile: p}))


@config.command("edit")
@click.argument("file", required=False,
                type=click.Choice(["providers", "routing"], case_sensitive=False))
def config_edit_cmd(file):
    """Open providers.json or routing.json in $EDITOR (default config created if missing)."""
    config_dir().mkdir(parents=True, exist_ok=True)
    if not providers_path().exists() or not routing_path().exists():
        init_config()
    if file is None:
        file = click.prompt("File to edit", type=click.Choice(["providers", "routing"]))
    target = providers_path() if file == "providers" else routing_path()
    backup = backup_file(target)
    if backup:
        click.echo(f"backup: {backup}")
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or shutil.which("nano")
    if not editor:
        die("no EDITOR set; edit config manually")
    import subprocess
    import sys
    if os.name == "nt":
        result = subprocess.run([editor, str(target)])
        sys.exit(result.returncode)
    else:
        os.execvp(editor, [editor, str(target)])


@config.command("login")
@click.argument("account", default="claude",
                type=click.Choice(["claude", "codex"], case_sensitive=False))
def config_login_cmd(account):
    """Log in a subscription account used by an 'auth' sentinel provider."""
    account = account.lower()
    if account == "codex":
        # The codex CLI owns its login (and its single-use refresh tokens);
        # awerouter only reads auth.json, so the login happens in the CLI.
        click.echo("codex logins live in the codex CLI — run: codex login")
        click.echo("awerouter picks the result up from $CODEX_HOME/auth.json automatically.")
        return
    from awerouter import claude
    if claude.login_status() is not None:
        click.echo("an existing claude login will be replaced (its tokens become useless).")
        if not click.confirm("Continue?"):
            click.echo("aborted")
            return
    url, verifier, state = claude.begin_login()
    click.echo("opening the browser to authorize awerouter as a Claude Code device…")
    click.echo("(if it does not open, visit the URL below)")
    click.echo()
    click.echo(f"  {url}")
    click.echo()
    import webbrowser
    webbrowser.open(url)
    code = click.prompt("After authorizing, paste the code shown on the callback page")
    try:
        payload = claude.complete_login(code.strip(), verifier, state)
    except claude.ClaudeAuthError as exc:
        raise SystemExit(f"awerouter: login failed: {exc}")
    from datetime import datetime, timezone
    expires = datetime.fromtimestamp(payload["expires_at"], tz=timezone.utc)
    click.echo(f"claude login saved: {claude.claude_auth_path()}")
    click.echo(f"  access token valid until {expires:%Y-%m-%d %H:%M} UTC (refreshed automatically)")


@config.command("logout")
@click.argument("account", default="claude",
                type=click.Choice(["claude", "codex"], case_sensitive=False))
def config_logout_cmd(account):
    """Remove a stored subscription login."""
    account = account.lower()
    if account == "codex":
        click.echo("codex logins live in the codex CLI — run: codex logout")
        return
    from awerouter import claude
    removed = claude.logout()
    if removed is None:
        click.echo(f"no claude login found ({claude.claude_auth_path()})")
        return
    click.echo(f"removed {removed}")


@config.command("restore")
@click.argument("file", required=False,
                type=click.Choice(["providers", "routing"], case_sensitive=False))
def config_restore_cmd(file):
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


def _keep_old_spelling(cmd: click.Command) -> click.Command:
    """Hidden top-level copy of a command that moved under `config`: the old
    `awerouter <name>` spelling keeps working, while the top-level help lists
    only the `config` form. A copy, not the same object — hiding the alias
    must not hide the subcommand."""
    alias = copy.copy(cmd)
    alias.hidden = True
    return alias


cli.add_command(_keep_old_spelling(config_login_cmd))
cli.add_command(_keep_old_spelling(config_logout_cmd))
cli.add_command(_keep_old_spelling(config_restore_cmd))


def _echo_merge_report(report: dict) -> None:
    if not (report["providers_added"] or report["profiles_added"] or report["settings_added"]):
        click.echo("nothing to merge; config already covers this template")
        return
    if report["providers_added"]:
        click.echo(f"providers added: {', '.join(report['providers_added'])}")
    if report["profiles_added"]:
        click.echo(f"profiles added: {', '.join(report['profiles_added'])}")
    if report["settings_added"]:
        click.echo(f"settings added: {', '.join(report['settings_added'])}")
    skipped = report["providers_skipped"] + report["profiles_skipped"]
    if skipped:
        click.echo(f"skipped (already present): {', '.join(skipped)}")
    if report["behavior_shift"]:
        click.echo()
        click.echo(
            "warning: newly set in settings: " + ", ".join(report["behavior_shift"])
            + " — global keys that re-route image/fall-through behavior for every"
            " existing profile. Undo with: awerouter config restore routing"
        )


@cli.command("init")
@click.argument("template", required=False, default="default")
@click.option("--merge", is_flag=True, default=False,
              help="Add a template's providers/profiles/settings to an existing config "
                   "(fill-missing; existing entries are never overwritten).")
def init_cmd(template, merge):
    """Create config from a bundled template (no argument: 'default')."""
    click.echo(f"template: {template}")
    if merge and (providers_path().exists() or routing_path().exists()):
        _echo_merge_report(merge_config(template))
    else:
        init_config(template)
    click.echo(config_dir())


def main(argv=None):
    try:
        return cli.main(args=argv, prog_name="awerouter")
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
