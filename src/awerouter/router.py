"""Request classifier and router.

Three-layer first-match-wins pipeline:

  L1 Capability guard  — web_search tool forces pro (flash can't run it)
  L2 Tier label match  — backgroundModel / thinkModel exact-match
  L3 Difficulty score  — long context / image -> pro; default -> flash (cost-first)
"""

from __future__ import annotations

import re

from awerouter.types import Destination, InspectResult, ResolveResult


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def inspect(body: dict) -> InspectResult:
    return InspectResult(
        token_count=_estimate_tokens(body),
        has_image=_has_image(body),
        has_web_search=_has_web_search(body),
        message_count=len(body.get("messages", [])),
    )


def resolve(
    model: str | None,
    body: dict,
    dests: dict[str, Destination],
    background_model: str,
    think_model: str,
    long_context_threshold: int,
    web_search_model: str = "pro",
) -> ResolveResult:
    feat = inspect(body)
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
    if feat.token_count > long_context_threshold:
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


# ---------------------------------------------------------------------------
# Feature extraction helpers
# ---------------------------------------------------------------------------

_CJK = re.compile(r"[一-鿿]")


def _estimate_tokens(body: dict) -> int:
    text = _extract_text(body)
    if not text:
        return 0
    total = len(text)
    cjk = len(_CJK.findall(text))
    non_cjk = total - cjk
    # non_cjk / 4 + cjk / 1.5  -> multiply by 12 to stay in int
    return (non_cjk * 3 + cjk * 8) // 12 or 1


def _extract_text(body: dict) -> str:
    parts: list[str] = []
    for msg in body.get("messages", []):
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
    return " ".join(parts)


def _has_image(body: dict) -> bool:
    for msg in body.get("messages", []):
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image":
                    return True
    return False


def _has_web_search(body: dict) -> bool:
    for tool in body.get("tools", []) or []:
        name = tool.get("name", "") if isinstance(tool, dict) else ""
        if name.startswith("web_search_"):
            return True
    return False
