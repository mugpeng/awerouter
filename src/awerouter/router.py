"""Request router.

First-match-wins pipeline over a precomputed InspectResult (extracted per
protocol by awerouter.protocols):

  L1 Capability guard  — web_search tool -> settings.webSearchModel (default pro)
  L2 Tier label match  — backgroundModel / thinkModel exact-match
  L3 Difficulty score  — long context / image -> pro; default -> flash (cost-first)
  L4 Tool-phase match  — last tool call search-class -> flash, edit-class -> pro

L4 sits below L3 on purpose: a session already above longContextThreshold
stays pro no matter what tool just ran (flash's capability ceiling and the
one-way flash->pro session invariant both win over tool-phase forcing).
"""

from __future__ import annotations

from awerouter.protocols import EDIT_TOOLS, FILE_SEARCH_TOOLS, effective_tokens
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
    tool_search_dest: str | None = "flash",
    tool_edit_dest: str | None = "pro",
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

    # L4: tool-phase match ------------------------------------------------
    # What the agent just did decides what the next turn is: search results
    # feed cheap mechanical next steps, a fresh edit means code is being
    # written or verified. Null destination disables a rule.
    if tool_edit_dest and feat.last_tool in EDIT_TOOLS:
        return ResolveResult(
            destination=tool_edit_dest,
            model=dests[tool_edit_dest].model,
            label="toolEdit",
            inspect=feat,
        )
    if tool_search_dest and feat.last_tool in FILE_SEARCH_TOOLS:
        return ResolveResult(
            destination=tool_search_dest,
            model=dests[tool_search_dest].model,
            label="toolSearch",
            inspect=feat,
        )

    return ResolveResult(
        destination="flash",
        model=dests["flash"].model,
        label="default",
        inspect=feat,
    )
