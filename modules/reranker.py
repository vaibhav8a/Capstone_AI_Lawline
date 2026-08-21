"""
Module 8 — Reranker Module
Uses cross-encoder/ms-marco-MiniLM-L-6-v1 to rerank candidate chunks.
"""

import logging
from typing import List, Dict, Any

from sentence_transformers.cross_encoder import CrossEncoder

import config

logger = logging.getLogger(__name__)

_reranker: CrossEncoder | None = None


def _get_reranker() -> CrossEncoder:
    """Lazy-load the cross-encoder model (singleton)."""
    global _reranker
    if _reranker is None:
        logger.info(f"[Reranker] Loading '{config.RERANKER_MODEL}' …")
        _reranker = CrossEncoder(config.RERANKER_MODEL, max_length=512)
        logger.info("[Reranker] Model loaded.")
    return _reranker


def rerank(
    query: str,
    candidates: List[Dict[str, Any]],
    top_k: int = config.RERANKER_TOP_K,
) -> List[Dict[str, Any]]:
    """
    Score each (query, candidate_text) pair with the cross-encoder,
    sort descending, return top_k results.

    Parameters
    ----------
    query      : raw user query string
    candidates : list of enriched chunk dicts (each must have 'text')
    top_k      : how many to return after reranking

    Returns
    -------
    Sorted list of chunk dicts (descending by reranker score),
    each augmented with '_reranker_score' field.
    """
    if not candidates:
        return []

    try:
        model = _get_reranker()
        pairs = [(query, c["text"]) for c in candidates]
        scores = model.predict(pairs, show_progress_bar=False)
        ranked = sorted(
            zip(candidates, scores),
            key=lambda x: x[1],
            reverse=True,
        )
        results = []
        for chunk, score in ranked[:top_k]:
            out = dict(chunk)
            out["_reranker_score"] = round(float(score), 4)
            results.append(out)
        logger.info(
            f"[Reranker] Reranked {len(candidates)} → {len(results)} chunks "
            f"(top score: {results[0]['_reranker_score'] if results else 'N/A'})"
        )
        return results
    except Exception as exc:
        logger.warning(
            "[Reranker] Cross-encoder unavailable (%s); using hybrid order.",
            exc,
        )
        results = []
        for i, chunk in enumerate(candidates[:top_k]):
            out = dict(chunk)
            out["_reranker_score"] = float(
                chunk.get("_retrieval_score", 1.0 - i * 0.01)
            )
            results.append(out)
        return results
