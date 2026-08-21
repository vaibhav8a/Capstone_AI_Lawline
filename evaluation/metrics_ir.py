"""
metrics_ir.py — standard information-retrieval metrics with binary relevance.

Evaluation is performed at the level of the **statutory section**, not the chunk.

Why: the chunking strategies under comparison emit different numbers of chunks
per section (fixed_window can emit several windows covering one section, while
section_whole emits exactly one). Scoring chunks directly would let a strategy
inflate precision purely by splitting the same provision into more pieces. The
unit a user actually wants is "did it find section 420", so each ranked chunk
list is collapsed to a ranked list of unique (document, section) pairs, first
occurrence winning, before any metric is computed.

All metrics take a ranked list of section keys and a set of gold section keys.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

SectionKey = tuple[str, str]


def dedupe_sections(ranked: Iterable[SectionKey]) -> list[SectionKey]:
    """Collapse a ranked chunk list to unique sections, preserving best rank."""
    seen: set[SectionKey] = set()
    out: list[SectionKey] = []
    for key in ranked:
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def precision_at_k(ranked: Sequence[SectionKey], gold: set[SectionKey], k: int) -> float:
    """Fraction of the top-k that is relevant.

    Denominator is min(k, len(ranked)) rather than k, so a system that returns
    fewer than k results is not penalised for the empty slots.
    """
    if not ranked:
        return 0.0
    top = ranked[:k]
    return sum(1 for key in top if key in gold) / len(top)


def recall_at_k(ranked: Sequence[SectionKey], gold: set[SectionKey], k: int) -> float:
    if not gold:
        return 0.0
    return sum(1 for key in ranked[:k] if key in gold) / len(gold)


def hit_rate_at_k(ranked: Sequence[SectionKey], gold: set[SectionKey], k: int) -> float:
    """1.0 if at least one gold section appears in the top-k."""
    return 1.0 if any(key in gold for key in ranked[:k]) else 0.0


def reciprocal_rank(ranked: Sequence[SectionKey], gold: set[SectionKey]) -> float:
    for index, key in enumerate(ranked, start=1):
        if key in gold:
            return 1.0 / index
    return 0.0


def ndcg_at_k(ranked: Sequence[SectionKey], gold: set[SectionKey], k: int) -> float:
    """Binary-relevance nDCG with the standard 1/log2(rank+1) discount.

    The ideal ranking places min(|gold|, k) relevant sections in the top slots.
    """
    if not gold:
        return 0.0
    dcg = sum(
        1.0 / math.log2(index + 1)
        for index, key in enumerate(ranked[:k], start=1)
        if key in gold
    )
    ideal_hits = min(len(gold), k)
    idcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0


def evaluate_query(
    ranked: Sequence[SectionKey],
    gold: set[SectionKey],
    ks: Sequence[int] = (1, 3, 5, 10),
) -> dict[str, float]:
    """All metrics for one query. Assumes `ranked` is already section-deduped."""
    scores: dict[str, float] = {"mrr": reciprocal_rank(ranked, gold)}
    for k in ks:
        scores[f"precision@{k}"] = precision_at_k(ranked, gold, k)
        scores[f"recall@{k}"] = recall_at_k(ranked, gold, k)
        scores[f"hit_rate@{k}"] = hit_rate_at_k(ranked, gold, k)
        scores[f"ndcg@{k}"] = ndcg_at_k(ranked, gold, k)
    return scores


def aggregate(per_query: list[dict[str, float]]) -> dict[str, float]:
    """Macro-average across queries (each query weighted equally)."""
    if not per_query:
        return {}
    keys = per_query[0].keys()
    return {key: sum(row[key] for row in per_query) / len(per_query) for key in keys}


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile; `pct` in [0, 100]."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def latency_summary(latencies_ms: Sequence[float]) -> dict[str, float]:
    if not latencies_ms:
        return {}
    return {
        "mean_ms": round(sum(latencies_ms) / len(latencies_ms), 2),
        "p50_ms": round(percentile(latencies_ms, 50), 2),
        "p95_ms": round(percentile(latencies_ms, 95), 2),
        "min_ms": round(min(latencies_ms), 2),
        "max_ms": round(max(latencies_ms), 2),
        "n": len(latencies_ms),
    }
