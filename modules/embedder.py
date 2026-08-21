"""
Module 3 — Embedding Module
Encodes chunk texts using BAAI/bge-base-en-v1.5 in batches.
"""

import logging
import numpy as np
from typing import List, Dict, Any

from tqdm import tqdm
from sentence_transformers import SentenceTransformer

import config

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    """Lazy-load the embedding model (singleton)."""
    global _model
    if _model is None:
        logger.info(f"[Embedder] Loading model '{config.EMBEDDING_MODEL}' …")
        _model = SentenceTransformer(config.EMBEDDING_MODEL)
        logger.info("[Embedder] Model loaded.")
    return _model


def encode_chunks(
    chunks: List[Dict[str, Any]],
    show_progress: bool = True,
) -> np.ndarray:
    """
    Generate L2-normalised embeddings for every chunk.

    Parameters
    ----------
    chunks        : enriched chunk dicts (must have 'text' key)
    show_progress : show tqdm progress bar

    Returns
    -------
    np.ndarray of shape (N, EMBEDDING_DIM), dtype float32
    """
    model = _get_model()
    texts = [c["text"] for c in chunks]

    logger.info(
        f"[Embedder] Encoding {len(texts)} chunks "
        f"(batch_size={config.EMBEDDING_BATCH}) …"
    )

    embeddings = model.encode(
        texts,
        batch_size=config.EMBEDDING_BATCH,
        normalize_embeddings=True,   # cosine via inner-product search in FAISS
        show_progress_bar=show_progress,
        convert_to_numpy=True,
    )

    embeddings = embeddings.astype(np.float32)
    logger.info(f"[Embedder] Embeddings shape: {embeddings.shape}")
    return embeddings


def encode_query(query: str) -> np.ndarray:
    """
    Encode a single retrieval query with the BGE instruction prefix.

    Returns
    -------
    np.ndarray of shape (1, EMBEDDING_DIM), dtype float32
    """
    model = _get_model()
    prefixed = config.BGE_QUERY_PREFIX + query
    vec = model.encode(
        [prefixed],
        normalize_embeddings=True,
        convert_to_numpy=True,
    ).astype(np.float32)
    return vec  # shape (1, dim)
