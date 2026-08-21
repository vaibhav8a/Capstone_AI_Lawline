"""
Module 4 — FAISS Index Module
Builds, persists, and queries a FAISS IndexFlatIP vector store.
"""

import logging
import pickle
import numpy as np
from pathlib import Path
from typing import List, Tuple, Dict, Any

import faiss

import config

logger = logging.getLogger(__name__)


class FAISSIndex:
    """
    Wraps a FAISS IndexFlatIP (inner-product cosine search on L2-normalised vecs).

    Usage
    -----
    idx = FAISSIndex.build(embeddings, chunks)
    idx.save()

    idx2 = FAISSIndex.load()
    chunk_dicts, scores = idx2.search(query_vec, top_k=10)
    """

    def __init__(self, index: faiss.Index, chunks: List[Dict[str, Any]]):
        self._index  = index
        self._chunks = chunks   # parallel list — position i = chunk i

    # ── Build ──────────────────────────────────────────────────────────────────

    @classmethod
    def build(
        cls,
        embeddings: np.ndarray,
        chunks: List[Dict[str, Any]],
    ) -> "FAISSIndex":
        """
        Create a new FAISS index from pre-computed embeddings.

        Parameters
        ----------
        embeddings  : (N, D) float32, already L2-normalised
        chunks      : corresponding list of enriched chunk dicts
        """
        n, dim = embeddings.shape
        if dim != config.EMBEDDING_DIM:
            logger.warning(
                f"[FAISS] Embedding dim {dim} != config dim {config.EMBEDDING_DIM}. "
                "Proceeding with actual dim."
            )

        logger.info(f"[FAISS] Building IndexFlatIP with {n} vectors (dim={dim}) …")
        index = faiss.IndexFlatIP(dim)
        index.add(embeddings)
        logger.info(f"[FAISS] Index contains {index.ntotal} vectors.")
        return cls(index, chunks)

    # ── Persist ────────────────────────────────────────────────────────────────

    def save(
        self,
        index_path: Path = config.FAISS_INDEX_PATH,
        meta_path: Path  = None,
    ) -> None:
        """Save FAISS binary + chunk metadata (pickle side-car)."""
        index_path = Path(index_path)
        index_path.parent.mkdir(parents=True, exist_ok=True)

        faiss.write_index(self._index, str(index_path))
        logger.info(f"[FAISS] Index saved → {index_path}")

        # Side-car: store chunks so we can recover metadata on load
        meta_path = meta_path or index_path.with_suffix(".meta.pkl")
        with open(meta_path, "wb") as fh:
            pickle.dump(self._chunks, fh)
        logger.info(f"[FAISS] Chunk metadata saved → {meta_path}")

    @classmethod
    def load(
        cls,
        index_path: Path = config.FAISS_INDEX_PATH,
        meta_path: Path  = None,
    ) -> "FAISSIndex":
        """Load persisted FAISS index and chunk metadata."""
        index_path = Path(index_path)
        meta_path  = meta_path or index_path.with_suffix(".meta.pkl")

        if not index_path.exists():
            raise FileNotFoundError(f"FAISS index not found: {index_path}")
        if not meta_path.exists():
            raise FileNotFoundError(f"FAISS metadata not found: {meta_path}")

        logger.info(f"[FAISS] Loading index from {index_path} …")
        index = faiss.read_index(str(index_path))

        with open(meta_path, "rb") as fh:
            chunks = pickle.load(fh)

        logger.info(f"[FAISS] Loaded {index.ntotal} vectors, {len(chunks)} chunks.")
        return cls(index, chunks)

    # ── Query ──────────────────────────────────────────────────────────────────

    def search(
        self,
        query_vec: np.ndarray,
        top_k: int = config.FAISS_TOP_K,
    ) -> Tuple[List[int], List[float]]:
        """
        Parameters
        ----------
        query_vec : (1, D) float32, L2-normalised

        Returns
        -------
        (indices, scores) — both lists of length ≤ top_k
        """
        query_vec = np.atleast_2d(query_vec).astype(np.float32)
        actual_k  = min(top_k, self._index.ntotal)

        scores, idxs = self._index.search(query_vec, actual_k)
        scores = scores[0].tolist()
        idxs   = idxs[0].tolist()

        # Filter invalid FAISS sentinels (-1)
        pairs = [(i, s) for i, s in zip(idxs, scores) if i >= 0]
        idxs, scores = zip(*pairs) if pairs else ([], [])
        return list(idxs), list(scores)

    def get_chunk(self, idx: int) -> Dict[str, Any]:
        return self._chunks[idx]

    @property
    def size(self) -> int:
        return self._index.ntotal
