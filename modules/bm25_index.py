"""
Module 5 — BM25 Index Module
Keyword-based retrieval using BM25Okapi (rank-bm25).
"""

import logging
import pickle
import re
from pathlib import Path
from typing import List, Tuple, Dict, Any

from rank_bm25 import BM25Okapi

import config

logger = logging.getLogger(__name__)

# Basic legal-aware stop words (extend as needed)
_STOP_WORDS = {
    "the", "a", "an", "and", "or", "of", "in", "to", "is", "are",
    "was", "were", "that", "this", "it", "be", "has", "have", "had",
    "for", "with", "on", "at", "by", "from", "as", "but", "not",
}


def _tokenize(text: str) -> List[str]:
    """Lowercase, strip punctuation, remove stop words."""
    tokens = re.findall(r"\b[a-zA-Z0-9]+\b", text.lower())
    return [t for t in tokens if t not in _STOP_WORDS]


class BM25Index:
    """
    Wraps BM25Okapi for keyword retrieval over legal chunks.

    Usage
    -----
    idx = BM25Index.build(chunks)
    idx.save()

    idx2 = BM25Index.load()
    results, scores = idx2.search("fundamental rights", top_k=10)
    """

    def __init__(self, bm25: BM25Okapi, chunks: List[Dict[str, Any]]):
        self._bm25   = bm25
        self._chunks = chunks

    # ── Build ──────────────────────────────────────────────────────────────────

    @classmethod
    def build(cls, chunks: List[Dict[str, Any]]) -> "BM25Index":
        logger.info(f"[BM25] Tokenising {len(chunks)} chunks …")
        tokenised = [_tokenize(c["text"]) for c in chunks]
        bm25 = BM25Okapi(tokenised)
        logger.info("[BM25] Index built.")
        return cls(bm25, chunks)

    # ── Persist ────────────────────────────────────────────────────────────────

    def save(self, path: Path = config.BM25_INDEX_PATH) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            pickle.dump({"bm25": self._bm25, "chunks": self._chunks}, fh)
        logger.info(f"[BM25] Index saved → {path}")

    @classmethod
    def load(cls, path: Path = config.BM25_INDEX_PATH) -> "BM25Index":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"BM25 index not found: {path}")
        with open(path, "rb") as fh:
            data = pickle.load(fh)
        logger.info(f"[BM25] Loaded index with {len(data['chunks'])} chunks.")
        return cls(data["bm25"], data["chunks"])

    # ── Query ──────────────────────────────────────────────────────────────────

    def search(
        self, query: str, top_k: int = config.BM25_TOP_K
    ) -> Tuple[List[int], List[float]]:
        """
        Parameters
        ----------
        query : raw query string (will be tokenised internally)

        Returns
        -------
        (indices, scores) — sorted descending by BM25 score, length ≤ top_k
        """
        tokens = _tokenize(query)
        if not tokens:
            return [], []

        raw_scores = self._bm25.get_scores(tokens)
        top_k = min(top_k, len(raw_scores))

        import numpy as np
        top_idxs = np.argsort(raw_scores)[::-1][:top_k].tolist()
        top_scores = [float(raw_scores[i]) for i in top_idxs]

        # Filter zero-score results
        pairs = [(i, s) for i, s in zip(top_idxs, top_scores) if s > 0.0]
        if not pairs:
            return [], []
        idxs, scores = zip(*pairs)
        return list(idxs), list(scores)

    def get_chunk(self, idx: int) -> Dict[str, Any]:
        return self._chunks[idx]
