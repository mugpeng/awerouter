"""Shared types for awerouter."""

from __future__ import annotations

from dataclasses import dataclass
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
    provider: Provider | None = None


@dataclass
class RoutingProfile:
    name: str                       # profile id, e.g. "cc-router-1"
    agent: str                      # maps to a providers.json group: "claude" / "codex"
    background_model: str
    think_model: str
    long_context_threshold: int
    destinations: dict[str, Destination]


@dataclass
class InspectResult:
    token_count: int
    has_image: bool
    has_web_search: bool
    message_count: int


@dataclass
class ResolveResult:
    destination: str
    provider: Provider
    model: str
    label: str
    inspect: InspectResult


@dataclass
class RequestLog:
    ts: str
    model_in: str
    label: str
    destination: str
    provider: str
    model_out: str
    status: Optional[int]
    ms: int
    bytes: int
    token_count: int
