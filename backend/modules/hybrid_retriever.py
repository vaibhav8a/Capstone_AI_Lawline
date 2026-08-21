"""
Module: hybrid_retriever.py
6-way parallel retrieval using ThreadPoolExecutor.
Fuses results using RRF and applies Court Hierarchy Multipliers.
"""

import logging
import time
from typing import List, Dict, Any, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))
import config

from .chroma_store import ChromaStore
from .bm25_index import BM25Index
from .knowledge_graph import KnowledgeGraph
from .embedder import encode_query

logger = logging.getLogger(__name__)

def _fuse_rrf(
    results_dict: Dict[str, List[Tuple[Any, float, Any]]],
    weights: Dict[str, float],
    k: int = 60
) -> Dict[str, float]:
    """
    Reciprocal Rank Fusion with weights.
    Returns dict mapping chunk_id -> fused_score.
    """
    rrf_scores = {}
    
    for source, scored_items in results_dict.items():
        weight = weights.get(source, 1.0)
        # Note: scored_items is a list of tuples. 
        # For Chroma: (chunk_id, score, meta)
        # For BM25: (chunk_id, score)
        # For KG: chunk dict directly, but we map to (chunk_id, 1.0) in the retriever
        
        # Sort by the score from the source to determine rank
        # Higher score is better in our returned formats
        sorted_items = sorted(scored_items, key=lambda x: x[1], reverse=True)
        
        for rank, item in enumerate(sorted_items, start=1):
            chunk_id = item[0]
            if isinstance(chunk_id, dict):
                chunk_id = chunk_id.get("chunk_id")
                
            if not chunk_id: continue
            
            score = weight * (1.0 / (k + rank))
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0.0) + score
            
    return rrf_scores

class HybridRetriever:
    def __init__(
        self,
        chroma: ChromaStore,
        bm25: BM25Index,
        kg: KnowledgeGraph,
        all_chunks: List[Dict[str, Any]]
    ):
        self._chroma = chroma
        self._bm25 = bm25
        self._kg = kg
        
        # Fast lookup mapping for final hydration (BM25/KG fallback)
        self._chunk_map = {c["chunk_id"]: c for c in all_chunks if "chunk_id" in c}

    def retrieve_with_stats(self, query: str, top_k: int = config.HYBRID_TOP_K) -> Tuple[List[Dict], Dict]:
        start = time.time()
        query_vec = encode_query(query)
        
        results_map = {}
        
        # 6-Way Parallel execution
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {
                pool.submit(self._chroma.search, query_vec, config.FAISS_TOP_K, "all"): "chroma_all",
                pool.submit(self._chroma.search, query_vec, 10, "ratio"): "chroma_ratio",
                pool.submit(self._chroma.search, query_vec, 10, "facts"): "chroma_facts",
                pool.submit(self._chroma.search, query_vec, 10, "citations"): "chroma_cits",
            }
            if self._bm25 is not None:
                futures[pool.submit(self._bm25.search, query, config.BM25_TOP_K)] = "bm25"
            if self._kg is not None:
                futures[pool.submit(self._kg.get_related_chunks, query, config.GRAPH_TOP_K)] = "kg"
            
            for fut in as_completed(futures, timeout=4.0):
                source = futures[fut]
                try:
                    res = fut.result()
                    if source == "bm25":
                        # BM25 returns (indices, scores). Convert to (chunk_id, score)
                        idxs, scores = res
                        standard_res = []
                        for i, s in zip(idxs, scores):
                            chunk = self._bm25.get_chunk(i)
                            standard_res.append((chunk["chunk_id"], s, chunk))
                        results_map[source] = standard_res
                    elif source == "kg":
                        # KG returns list of chunk dicts. Assign a flat score.
                        standard_res = [(c["chunk_id"], 1.0, c) for c in res]
                        results_map[source] = standard_res
                    else:
                        # Chroma returns (chunk_id, score, meta)
                        results_map[source] = res
                except Exception as e:
                    logger.warning(f"[HybridRetriever] Source '{source}' failed or timed out: {e}")

        # Weights mapping
        weights = {
            "chroma_all": config.WEIGHT_CHROMA_ALL,
            "chroma_ratio": config.WEIGHT_CHROMA_RATIO,
            "chroma_facts": config.WEIGHT_CHROMA_FACTS,
            "chroma_cits": config.WEIGHT_CHROMA_CITS,
            "bm25": config.WEIGHT_BM25,
            "kg": config.WEIGHT_GRAPH
        }

        # RRF Fusion
        fused_scores = _fuse_rrf(results_map, weights, k=config.RRF_K)
        
        # Hydrate chunks and apply Court Hierarchy Multipliers
        payload_map = {}
        for items in results_map.values():
            for cid, _score, payload in items:
                if cid not in payload_map and isinstance(payload, dict):
                    payload_map[cid] = payload

        hydrated_results = []
        for cid, score in fused_scores.items():
            base_chunk = payload_map.get(cid) or self._chunk_map.get(cid)
            if base_chunk:
                chunk = dict(base_chunk)
                chunk["chunk_id"] = cid
                
                # Apply jurisdiction hierarchy multiplier
                court = chunk.get("court", "").upper()
                multiplier = 1.0
                for c_key, mult in config.COURT_MULTIPLIERS.items():
                    if c_key in court:
                        multiplier = mult
                        break
                        
                final_score = score * multiplier
                chunk["_retrieval_score"] = float(final_score)
                hydrated_results.append(chunk)

        # Final sort by score
        hydrated_results.sort(key=lambda x: x["_retrieval_score"], reverse=True)
        final_top = hydrated_results[:top_k]
        
        latency = (time.time() - start) * 1000
        stats = {
            "retrieved_count": len(final_top),
            "sources": {k: len(v) for k, v in results_map.items()},
            "latency_ms": latency
        }
        
        return final_top, stats
