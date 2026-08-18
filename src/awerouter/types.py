"""Shared types for awerouter."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Provider:
    name: str
    base_url: str
    auth: str
    # Auto-detected from base_url at load time (anthropic.com → x-api-key, else authorization).
    # Explicit override only when the heuristic is wrong.
    auth_header: str = "authorization"


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
    """Global routing settings (shared across all profiles)."""
    background_model: str = "flash"   # L2 tier-label for background → flash dest
    think_model: str = "pro"          # L2 tier-label for think → pro dest
    web_search_model: str = "pro"     # L1 web_search destination key (legacy alias of toolRouting.webSearch)
    search_result_discount: float = 0.3  # L3 weight of file-search (Grep/Glob/LS) result tokens; 1 = off
    long_context_auto: AutoThresholdConfig = field(default_factory=AutoThresholdConfig)
    tool_routing: ToolRoutingConfig = field(default_factory=ToolRoutingConfig)


@dataclass
class RoutingProfile:
    name: str                       # profile id, e.g. "cc-router-1"
    protocol: str                   # maps to a providers.json group: anthropic / openai-chat / openai-responses
    long_context_threshold: int     # when threshold_auto: fallback until serve start resolves it
    destinations: dict[str, Destination]
    port: Optional[int] = None      # fixed listen port; --port overrides, else default 20128
    threshold_auto: bool = False    # longContextThreshold was "auto"; resolved at serve start
    rtk: bool = False               # compress tool_result content before routing (opt-in)


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


@dataclass
class ResolveResult:
    destination: str
    model: str
    label: str
    inspect: InspectResult


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
