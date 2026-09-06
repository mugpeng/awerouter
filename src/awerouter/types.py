"""Shared types for awerouter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Provider:
    name: str
    base_url: str
    # None = no-auth upstream (local model servers); request goes out without any auth header.
    auth: Optional[str]
    # Auto-detected from base_url at load time (anthropic.com → x-api-key, else authorization).
    # Explicit override only when the heuristic is wrong.
    auth_header: str = "authorization"
    # Models this provider can directly serve under gateway mode ('provider/<model>'
    # names). Empty = provider only reachable through a routing profile's destination.
    models: tuple[str, ...] = ()
    # Provider can receive image content (failover candidates for image-bearing
    # requests are filtered on it). Default False: an undeclared provider is
    # never handed images during failover — the safe direction.
    multimodal: bool = False
    # Account-pool tag: entries sharing it within one protocol group are the
    # same vendor's accounts (same models, different keys). A gateway
    # 'provider/<model>' forward fails over to fellow members — same model,
    # declaration order wrapping from the named entry. Empty = pinned.
    pool: str = ""


@dataclass
class Destination:
    provider_name: str
    model: str


@dataclass
class AutoThresholdConfig:
    """Policy for longContextThreshold: "auto" (routing.json settings.longContextAuto).

    The threshold is the given percentile of the profile's own L3 effective-token
    distribution over the trailing window; with fewer samples than minSamples the
    fallbackThreshold applies (cold start must not behave erratically).
    """
    percentile: int = 95            # which percentile of L3 tokens becomes the threshold
    window_days: int = 7            # only samples newer than this count
    min_samples: int = 50           # below this many L3 samples → fallbackThreshold
    fallback_threshold: int = 8000


@dataclass
class ToolRoutingConfig:
    """Forced routing keyed on tools (routing.json settings.toolRouting).

    Values are destination keys ("flash"/"pro"); null disables that rule.
    "webSearch" fires when the request declares a web_search tool (capability
    guard, highest precedence); null falls back to the legacy
    settings.webSearchModel. "edit" is the L4 consequence checkpoint: the
    turn after the trailing tool batch changed code (Edit/Write/apply_patch/
    ...) goes to pro — flash drafts, pro reviews. It sits below L3:
    long-context sessions stay pro.
    """
    web_search: Optional[str] = None    # None = legacy settings.webSearchModel
    edit: Optional[str] = "pro"


@dataclass
class Settings:
    """Routing settings — the global `settings` block in routing.json.

    Every key may also be set per profile (a `settings` block inside the
    profile body): missing keys inherit from this global block, so a profile
    re-tunes only what differs. `RoutingProfile.settings` carries the merged
    result the router actually uses."""
    background_model: str = "flash"   # L2 tier-label for background → flash dest
    think_model: str = "pro"          # L2 tier-label for think → pro dest
    web_search_model: str = "pro"     # L1 web_search destination key (legacy alias of toolRouting.webSearch)
    image_model: str = "pro"          # L1 destination key for image-bearing requests (multimodal sidekick: flash)
    default_model: str = "flash"      # fall-through destination key (pro-first profiles: pro)
    search_result_discount: float = 0.3  # L3 weight of file-search (Grep/Glob/LS) result tokens; 1 = off
    image_bridge: bool = False       # flash transcribes history images to text;
                                     # a text-only pro continues image sessions (opt-in)
    long_context_auto: AutoThresholdConfig = field(default_factory=AutoThresholdConfig)
    tool_routing: ToolRoutingConfig = field(default_factory=ToolRoutingConfig)
    # Gateway-only (routing.json top-level "defaultProfile"): the profile bare
    # model names (auto/flash/pro) resolve to under `serve all`. Attached by
    # load_routing() to the GLOBAL settings only — per-profile Settings copies
    # leave it None, and a profile body writing the key dies as unknown.
    default_profile: Optional[str] = None


@dataclass
class OdcpConfig:
    """odcp profile flag, normalized (routing.json: "odcp": true | {...}).

    Cross-message context pruning beside rtk's per-text compression: dedup
    keeps only the newest output of repeated identical tool calls; purgeErrors
    strips a failed call's input strings once it is `purge_turns` user
    messages old (the error text stays). The trailing turn is never touched.
    """
    dedup: bool = True
    purge_errors: bool = True
    purge_turns: int = 4


@dataclass
class RoutingProfile:
    name: str                       # profile id, e.g. "cc-router-1"
    # Maps to providers.json groups: anthropic / openai-chat / openai-responses.
    # A list serves several protocols on one port — clients pick by endpoint
    # path (/v1/messages vs /v1/chat/completions vs /v1/responses); every
    # destination provider must exist in each served group.
    protocols: "str | list[str] | tuple[str, ...]"
    long_context_threshold: int     # when threshold_auto: fallback until serve start resolves it
    destinations: dict[str, Destination]
    port: Optional[int] = None      # fixed listen port; --port overrides, else default 20128
    threshold_auto: bool = False    # longContextThreshold was "auto"; resolved at serve start
    rtk: bool = False               # compress tool_result content before routing (opt-in)
    odcp: "OdcpConfig | None" = None  # dedup + errored-call input purge before routing (None = off)
    # Effective settings = global settings merged with this profile's overrides
    # (what serve and the router use); the raw override keys below are
    # display-only — settings keys configured directly in the profile body.
    settings: Settings = field(default_factory=Settings)
    settings_overrides: dict = field(default_factory=dict)
    # Ordered failover queues keyed by destination key, from routing.json
    # "backups". Empty = zero-config: the tier queues carry one implicit
    # cross-tier hop each (flash→pro, pro→flash). An explicit list replaces
    # the implicit hop for that tier — exactly what was written, nothing
    # appended.
    backups: dict = field(default_factory=dict)

    def __post_init__(self):
        # Accept a bare string everywhere a list works ("anthropic" == ["anthropic"]).
        self.protocols = (self.protocols,) if isinstance(self.protocols, str) else tuple(self.protocols)

    @property
    def protocol(self) -> str:
        """Display form: protocol ids joined with '+' ("anthropic+openai-chat")."""
        return "+".join(self.protocols)


@dataclass
class InspectResult:
    token_count: int
    has_image: bool
    has_web_search: bool
    message_count: int
    # Per-content-type token estimate (system/messages/tools/tool_results/
    # tool_calls/thinking); sum equals token_count. Empty types omitted.
    token_breakdown: dict = field(default_factory=dict)
    # Estimated tokens of tool results from file-search tools (Grep/Glob/LS);
    # L3 weighs these against the threshold at settings.searchResultDiscount.
    file_search_tokens: int = 0
    # Trailing parallel batch of tool calls in the history (lowercased names,
    # empty tuple if none) and its strongest phase — "edit" when any call in
    # it changed code, else ""; the L4 consequence checkpoint keys on it.
    last_tools: tuple = ()
    last_phase: str = ""
    # Image present in the FINAL message — a fresh upload this turn (routed to
    # the multimodal imageModel natively); has_image alone means stale history
    # (bridgeable to text when settings.imageBridge is on).
    has_new_image: bool = False


@dataclass
class ResolveResult:
    destination: str
    model: str
    label: str
    inspect: InspectResult


@dataclass
class Candidate:
    """One hop of a profile's failover queue (built after resolve picks a tier).

    tier is the cost tier the hop serves as ("flash"/"pro"; "direct" for
    gateway provider/<model> forwards) — what RequestLog.destination records —
    while dest carries the concrete provider+model that serves it.
    """
    tier: str
    dest: Destination


@dataclass
class RequestLog:
    ts: str
    request_id: str
    model_in: str
    label: str
    destination: str
    provider: str
    model_out: str
    status: Optional[int]
    ms: int                                   # time to first response byte
    bytes: int
    token_count: int
    profile: str = ""
    duration_ms: int = 0                      # full request duration incl. streaming (0 = not recorded)
    protocol: str = ""                        # wire protocol served (anthropic / openai-chat / openai-responses)
    agent: str = ""                           # normalized client identity from the User-Agent header
    tokens: dict = field(default_factory=dict)  # per-type input-token breakdown; sum == token_count (pre-breakdown logs: empty)
    file_search_tokens: int = 0                  # estimated tokens of file-search tool results (0 = none / legacy log)
    rtk_saved: int = 0                           # estimated input tokens saved by rtk compression (0 = off / none / legacy log)
    odcp_saved: int = 0                          # estimated input tokens saved by odcp pruning (0 = off / none / legacy log)
    codex_retried: bool = False                  # an upstream 401 triggered a subscription-login retry (codex re-read / claude refresh; False = no / legacy log)
    fallback_hops: int = 0                       # failover hops taken before the response (0 = primary / legacy log)
