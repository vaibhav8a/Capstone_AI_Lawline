"""
Module: reranker.py
Two-pass reranking: Core MS-Marco cross-encoder + Ratio/Obiter semantic boost.
"""

import logging
from typing import List, Dict, Any

from sentence_transformers import CrossEncoder
import torch

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))
import config

logger = logging.getLogger(__name__)

_RERANKER_MODEL = None

def _get_reranker():
    global _RERANKER_MODEL
    if _RERANKER_MODEL is None:
        logger.info(f"[Reranker] Loading cross-encoder: {config.RERANKER_MODEL}")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _RERANKER_MODEL = CrossEncoder(config.RERANKER_MODEL, device=device)
    return _RERANKER_MODEL

def rerank_two_pass(query: str, candidates: List[Dict[str, Any]], top_k: int = config.RERANKER_TOP_K) -> List[Dict[str, Any]]:
    """
    Pass 1: Cross-encoder scores all candidates against the query.
    Pass 2: Apply a multiplier (RATIO_SECTION_BOOST) to chunks classified as "ratio".
    Returns the top_k sorted chunks with ['_reranker_score'] populated.
    """
    if not candidates:
        return []
        
    model = _get_reranker()
    
    # Pass 1: Raw MS-Marco scores
    # Provide the LLM context parent_text if available, otherwise child text
    texts = [c.get("parent_text") or c.get("text", "") for c in candidates]
    pairs = [(query, text) for text in texts]
    
    batch_size = 32
    raw_scores = []
    
    logger.debug(f"[Reranker] Scoring {len(pairs)} pairs...")
    
    # Predict in batches
    for i in range(0, len(pairs), batch_size):
        batch = pairs[i:i+batch_size]
        scores = model.predict(batch)
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        elif not isinstance(scores, list):
            scores = [scores]
        raw_scores.extend(scores)
        
    # Pass 2: Semantic Boosts
    boosted_candidates = []
    
    for chunk, raw_score in zip(candidates, raw_scores):
        multiplier = 1.0
        
        sec_type = chunk.get("section_type", "other")
        
        # Boost ratio elements so bindings float above dictionary definitions
        if sec_type == "ratio":
            multiplier = config.RATIO_SECTION_BOOST
            
        # Optional: Further down-weight "obiter" to de-emphasize passing remarks
        if sec_type == "obiter":
            multiplier = 0.90
            
        final_score = float(raw_score) * multiplier
        
        out_chunk = dict(chunk)
        out_chunk["_reranker_raw"] = float(raw_score)
        out_chunk["_reranker_score"] = float(final_score)
        boosted_candidates.append(out_chunk)
        
    # Sort descending
    boosted_candidates.sort(key=lambda x: x["_reranker_score"], reverse=True)
    
    # Truncate
    top_candidates = boosted_candidates[:top_k]
    
    logger.info(f"[Reranker] Returned top {len(top_candidates)} from an initial pool of {len(candidates)}")
    
    return top_candidates
