from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Callable, Iterable

from ..dedup.normalize import normalize_model_slug
from .types import MatchResult, ModelBenchmarkRow, SourceKey


MATCH_BLOCKLIST = [
    "trinity",
    "aurora-alpha",
    "healer-alpha",
    "hunter-alpha",
    "bodybuilder",
    "sample-spec",
    "auto-model",
    "free",
    "openrouter-auto",
    "nano-gpt",
]


def preprocess_benchmark_model_name(s: str) -> str:
    # Remove UI glyphs and common parenthetical qualifiers that are not part of the canonical id,
    # e.g. "gpt-5 (high)" -> "gpt-5".
    s = (s or "").strip()
    if not s:
        return s
    # Drop common leading table markers.
    s = s.lstrip("▶•*-—–· ").strip()
    # Drop trailing parentheticals like "(high)", "(thinking)", etc.
    import re

    s = re.sub(r"\s*\([^)]*\)\s*$", "", s).strip()
    return s


def _ratio_fallback(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio() * 100.0


def _make_ratio() -> Callable[[str, str], float]:
    try:
        from rapidfuzz.fuzz import ratio as rf_ratio  # type: ignore

        def _rf(a: str, b: str) -> float:
            return float(rf_ratio(a, b))

        return _rf
    except Exception:
        return _ratio_fallback


@dataclass(frozen=True)
class CanonicalIndex:
    normalized_to_model_ids: dict[str, list[str]]
    all_keys: list[str]
    model_id_to_meta: dict[str, tuple[str | None, str | None, str | None]]


def build_canonical_index(
    model_rows: Iterable[tuple[str, str | None, str | None, str | None]],
) -> CanonicalIndex:
    # model_rows: (model_id, model_name, developer, model_family)
    norm_to_ids: dict[str, list[str]] = {}
    # Always index by canonical model_id (should be unique after Phase 1 dedup).
    model_name_norms: dict[str, set[str]] = {}
    model_id_to_meta: dict[str, tuple[str | None, str | None, str | None]] = {}
    for model_id, model_name, developer, model_family in model_rows:
        model_id_to_meta[model_id] = (model_name, developer, model_family)
        nk = normalize_model_slug(model_id)
        if nk:
            norm_to_ids.setdefault(nk, []).append(model_id)
        nmn = normalize_model_slug(model_name or "")
        if nmn:
            model_name_norms.setdefault(nmn, set()).add(model_id)

    # Index by model_name only when it maps uniquely to one canonical model_id.
    for nmn, ids in model_name_norms.items():
        if len(ids) != 1:
            continue
        (only_id,) = tuple(ids)
        if nmn in norm_to_ids:
            continue
        norm_to_ids[nmn] = [only_id]
    keys = sorted(norm_to_ids.keys())
    return CanonicalIndex(
        normalized_to_model_ids=norm_to_ids,
        all_keys=keys,
        model_id_to_meta=model_id_to_meta,
    )


def _is_blocklisted(norm_slug: str) -> str | None:
    s = norm_slug or ""
    for term in MATCH_BLOCKLIST:
        if term in s:
            return term
    return None


def _developer_family_mismatch(norm_benchmark: str, *, candidate_model_id: str, index: CanonicalIndex) -> str | None:
    meta = index.model_id_to_meta.get(candidate_model_id) or (None, None, None)
    _, developer, family = meta
    cand_s = " ".join(
        [
            normalize_model_slug(candidate_model_id),
            normalize_model_slug(developer or ""),
            normalize_model_slug(family or ""),
        ]
    )
    bench = norm_benchmark or ""
    hints: list[tuple[str, list[str]]] = [
        ("claude", ["anthropic", "claude"]),
        ("gpt", ["openai", "gpt", "o1", "o3"]),
        ("gemini", ["google", "gemini"]),
        ("qwen", ["qwen", "alibaba"]),
        ("deepseek", ["deepseek"]),
        ("mistral", ["mistral"]),
        ("llama", ["llama", "meta"]),
        ("grok", ["grok", "xai"]),
        ("kimi", ["kimi", "moonshot"]),
        ("glm", ["glm", "zhipu", "z-ai", "zai", "z-ai"]),
        ("minimax", ["minimax"]),
    ]
    for token, allow in hints:
        if token not in bench:
            continue
        if any(a in cand_s for a in allow):
            return None
        return f"developer/family mismatch for token '{token}'"
    return None


def match_model_name(
    source: SourceKey,
    raw_name: str,
    *,
    index: CanonicalIndex,
    auto_threshold: float = 75.0,
    review_threshold: float = 60.0,
) -> MatchResult:
    raw_clean = preprocess_benchmark_model_name(raw_name)
    norm = normalize_model_slug(raw_clean)
    if not norm:
        return MatchResult(
            source=source,
            raw_name=raw_name,
            normalized_name=norm,
            status="unmatched",
            model_id=None,
            score=None,
            reason="empty normalized name",
        )

    term = _is_blocklisted(norm)
    if term:
        return MatchResult(
            source=source,
            raw_name=raw_name,
            normalized_name=norm,
            status="unmatched",
            model_id=None,
            score=None,
            reason=f"blocklisted: {term}",
        )

    exact = index.normalized_to_model_ids.get(norm)
    if exact:
        if len(exact) == 1:
            return MatchResult(
                source=source,
                raw_name=raw_name,
                normalized_name=norm,
                status="matched",
                model_id=exact[0],
                score=100.0,
                reason="exact normalized match",
            )
        return MatchResult(
            source=source,
            raw_name=raw_name,
            normalized_name=norm,
            status="needs_review",
            model_id=exact[0],
            score=100.0,
            reason=f"ambiguous exact match: {len(exact)} candidates",
        )

    ratio = _make_ratio()
    best_key: str | None = None
    best_score: float = -1.0
    for k in index.all_keys:
        s = ratio(norm, k)
        if s > best_score:
            best_key = k
            best_score = s

    if best_key is None:
        return MatchResult(
            source=source,
            raw_name=raw_name,
            normalized_name=norm,
            status="unmatched",
            model_id=None,
            score=None,
            reason="no canonical keys",
        )

    candidate_ids = index.normalized_to_model_ids.get(best_key) or []
    candidate_id = candidate_ids[0] if candidate_ids else None

    # Version/date guardrail: if the benchmark row does not include a specific date version,
    # avoid auto-matching to a dated canonical id (and vice versa).
    import re

    date_rx = re.compile(r"(20\\d{2}-\\d{2}-\\d{2})")
    raw_dates = set(date_rx.findall(norm))
    cand_dates = set(date_rx.findall(best_key))
    if raw_dates != cand_dates:
        if raw_dates or cand_dates:
            # If either side is dated and they don't match exactly, treat as review/unmatched.
            if best_score >= review_threshold and candidate_id:
                return MatchResult(
                    source=source,
                    raw_name=raw_name,
                    normalized_name=norm,
                    status="needs_review",
                    model_id=candidate_id,
                    score=best_score,
                    reason=f"version/date mismatch to {best_key}",
                )
            return MatchResult(
                source=source,
                raw_name=raw_name,
                normalized_name=norm,
                status="unmatched",
                model_id=None,
                score=best_score,
                reason=f"version/date mismatch to {best_key}",
            )
    if best_score >= auto_threshold and candidate_id:
        mismatch = _developer_family_mismatch(norm, candidate_model_id=candidate_id, index=index)
        if mismatch:
            return MatchResult(
                source=source,
                raw_name=raw_name,
                normalized_name=norm,
                status="needs_review",
                model_id=candidate_id,
                score=best_score,
                reason=mismatch,
            )
        return MatchResult(
            source=source,
            raw_name=raw_name,
            normalized_name=norm,
            status="matched",
            model_id=candidate_id,
            score=best_score,
            reason=f"fuzzy match to {best_key}",
        )
    if best_score >= review_threshold and candidate_id:
        return MatchResult(
            source=source,
            raw_name=raw_name,
            normalized_name=norm,
            status="needs_review",
            model_id=candidate_id,
            score=best_score,
            reason=f"fuzzy needs review to {best_key}",
        )
    return MatchResult(
        source=source,
        raw_name=raw_name,
        normalized_name=norm,
        status="unmatched",
        model_id=None,
        score=best_score,
        reason=f"best fuzzy {best_key}",
    )


def match_benchmark_rows(
    rows: Iterable[ModelBenchmarkRow],
    *,
    index: CanonicalIndex,
    auto_threshold: float = 75.0,
    review_threshold: float = 60.0,
) -> list[tuple[ModelBenchmarkRow, MatchResult]]:
    out: list[tuple[ModelBenchmarkRow, MatchResult]] = []
    for r in rows:
        m = match_model_name(
            r.source,
            r.model_name_raw,
            index=index,
            auto_threshold=auto_threshold,
            review_threshold=review_threshold,
        )
        out.append((r, m))
    return out
