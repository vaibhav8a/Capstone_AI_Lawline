"""
Module 7 — Hybrid Retrieval Module
Fuses FAISS, BM25, and Knowledge Graph results via Reciprocal Rank Fusion (RRF).
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Tuple

import numpy as np

import config
from modules.faiss_index    import FAISSIndex
from modules.bm25_index     import BM25Index
from modules.knowledge_graph import KnowledgeGraph
from modules.embedder        import encode_query

logger = logging.getLogger(__name__)


# ── RRF helpers ───────────────────────────────────────────────────────────────

def _rrf_score(rank: int, k: int = config.RRF_K) -> float:
    """Reciprocal Rank Fusion score for a result at 1-based *rank*."""
    return 1.0 / (k + rank)


def _fuse_rrf(
    ranked_lists: List[List[int]],
    weights: List[float],
    all_chunks: List[Dict[str, Any]],
) -> List[Tuple[int, float]]:
    """
    Merge multiple ranked lists with weighted RRF.

    Parameters
    ----------
    ranked_lists : each inner list is a list of chunk indices in ranked order
    weights      : per-list weight (must match length of ranked_lists)
    all_chunks   : master chunk list used only to bound valid indices

    Returns
    -------
    Sorted list of (chunk_index, fused_score) descending.
    """
    scores: Dict[int, float] = {}

    for ranked, w in zip(ranked_lists, weights):
        for rank_0based, idx in enumerate(ranked):
            if idx < 0 or idx >= len(all_chunks):
                continue
            s = _rrf_score(rank_0based + 1) * w
            scores[idx] = scores.get(idx, 0.0) + s

    sorted_results = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_results


# ── Hybrid Retriever ──────────────────────────────────────────────────────────

class HybridRetriever:
    """
    Orchestrates parallel retrieval from FAISS, BM25, and KG,
    then fuses results via weighted RRF.
    """

    def __init__(
        self,
        faiss_idx: FAISSIndex,
        bm25_idx:  BM25Index,
        kg:        KnowledgeGraph,
        all_chunks: List[Dict[str, Any]],
    ):
        self._faiss  = faiss_idx
        self._bm25   = bm25_idx
        self._kg     = kg
        self._chunks = all_chunks  # master list for index→chunk lookup

    def retrieve(
        self,
        query: str,
        top_k: int = config.HYBRID_TOP_K,
    ) -> List[Dict[str, Any]]:
        """
        Run all three retrievers concurrently and return *top_k*
        deduplicated chunks sorted by fused RRF score.
        """
        results, _stats = self.retrieve_with_stats(query=query, top_k=top_k)
        return results

    def retrieve_with_stats(
        self,
        query: str,
        top_k: int = config.HYBRID_TOP_K,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Same as `retrieve()`, but also returns lightweight diagnostics
        useful for the Analytics UI.
        """

        # ── 1. Encode query ──────────────────────────────────────────────────
        query_vec = encode_query(query)

        # ── 2. Run retrievers in parallel ────────────────────────────────────
        faiss_idxs: List[int] = []
        bm25_idxs: List[int] = []
        graph_chunks: List[Dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                pool.submit(
                    self._faiss.search, query_vec, config.FAISS_TOP_K
                ): "faiss",
                pool.submit(
                    self._bm25.search, query, config.BM25_TOP_K
                ): "bm25",
                pool.submit(
                    self._kg.get_related_chunks, query, config.GRAPH_TOP_K
                ): "graph",
            }

            for fut in as_completed(futures):
                source = futures[fut]
                try:
                    result = fut.result()
                    if source == "faiss":
                        faiss_idxs, _ = result
                    elif source == "bm25":
                        bm25_idxs, _ = result
                    else:
                        graph_chunks = result
                except Exception as exc:
                    logger.warning(
                        f"[HybridRetriever] {source} retrieval failed: {exc}"
                    )

        # ── 3. Map graph chunks to global indices ────────────────────────────
        # Build a quick lookup: chunk_id → global list index
        chunk_id_to_idx = {c.get("chunk_id", str(i)): i
                           for i, c in enumerate(self._chunks)}

        graph_idxs: List[int] = []
        for gc in graph_chunks:
            cid = gc.get("chunk_id", "")
            idx = chunk_id_to_idx.get(cid, -1)
            if idx >= 0:
                graph_idxs.append(idx)

        # ── 4. RRF Fusion ─────────────────────────────────────────────────────
        fused = _fuse_rrf(
            ranked_lists=[list(faiss_idxs), list(bm25_idxs), graph_idxs],
            weights=[config.WEIGHT_FAISS, config.WEIGHT_BM25, config.WEIGHT_GRAPH],
            all_chunks=self._chunks,
        )

        # ── 5. Deduplicate & trim ─────────────────────────────────────────────
        seen: set = set()
        results: List[Dict[str, Any]] = []

        for idx, score in fused:
            if idx in seen:
                continue
            seen.add(idx)
            chunk = dict(self._chunks[idx])  # shallow copy
            chunk["_retrieval_score"] = round(score, 6)
            results.append(chunk)
            if len(results) >= top_k:
                break

        graph_used = len(graph_idxs) > 0
        stats: Dict[str, Any] = {
            "faiss_candidates": len(faiss_idxs),
            "bm25_candidates": len(bm25_idxs),
            "graph_candidates": len(graph_idxs),
            "graph_used": graph_used,
        }

        logger.info(
            "[HybridRetriever] Fused %s FAISS + %s BM25 + %s Graph → %s candidates",
            len(faiss_idxs),
            len(bm25_idxs),
            len(graph_idxs),
            len(results),
        )
        return results, stats
