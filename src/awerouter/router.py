"""Request router.

Three-layer first-match-wins pipeline over a precomputed InspectResult
(extracted per protocol by awerouter.protocols):

  L1 Capability guard  — web_search tool -> settings.webSearchModel (default pro)
  L2 Tier label match  — backgroundModel / thinkModel exact-match
  L3 Difficulty score  — long context / image -> pro; default -> flash (cost-first)
"""

from __future__ import annotations

from awerouter.protocols import effective_tokens
from awerouter.types import Destination, InspectResult, ResolveResult


def resolve(
    model: str | None,
    feat: InspectResult,
    dests: dict[str, Destination],
    background_model: str,
    think_model: str,
    long_context_threshold: int,
    web_search_model: str = "pro",
    search_discount: float = 0.3,
) -> ResolveResult:
    m = model or ""

    # L1: capability guard ------------------------------------------------
    if feat.has_web_search:
        dest_key = web_search_model
        return ResolveResult(
            destination=dest_key,
            model=dests[dest_key].model,
            label="webSearch",
            inspect=feat,
        )

    # L2: tier label match ------------------------------------------------
    if m == background_model:
        return ResolveResult(
            destination="flash",
            model=dests["flash"].model,
            label="background",
            inspect=feat,
        )
    if m == think_model:
        return ResolveResult(
            destination="pro",
            model=dests["pro"].model,
            label="think",
            inspect=feat,
        )

    # L3: difficulty score (cost-first: default -> flash) -----------------
    # File-search results (Grep/Glob/LS) count at settings.searchResultDiscount:
    # bulk they add is cheap for flash to carry, so they must not alone tip the
    # scale to pro.
    if effective_tokens(feat.token_count, feat.file_search_tokens, search_discount) > long_context_threshold:
        return ResolveResult(
            destination="pro",
            model=dests["pro"].model,
            label="longContext",
            inspect=feat,
        )
    if feat.has_image:
        return ResolveResult(
            destination="pro",
            model=dests["pro"].model,
            label="image",
            inspect=feat,
        )

    return ResolveResult(
        destination="flash",
        model=dests["flash"].model,
        label="default",
        inspect=feat,
    )
