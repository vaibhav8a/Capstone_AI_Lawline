"""
Module: embedder.py
Encodes text chunks using SentenceTransformers (BGE-base) with FP16 support.
Injects synonyms via LegalDictionary before embedding.
"""

import logging
from typing import List, Dict, Any
import numpy as np

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))
import config

from .legal_dictionary import LegalDictionary

logger = logging.getLogger(__name__)

# Global cache to avoid reloading the model
_MODEL_INSTANCE = None
_DICTIONARY = None

def _get_model():
    global _MODEL_INSTANCE
    if _MODEL_INSTANCE is None:
        from sentence_transformers import SentenceTransformer
        import torch
        logger.info(f"[Embedder] Loading embedding model: {config.EMBEDDING_MODEL}")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        # BGE models work well with FP16
        _MODEL_INSTANCE = SentenceTransformer(config.EMBEDDING_MODEL, device=device)
        if device == "cuda":
            _MODEL_INSTANCE.half() # Force FP16
        logger.info(f"[Embedder] Model loaded on {device}")
    return _MODEL_INSTANCE

def _get_dictionary():
    global _DICTIONARY
    if _DICTIONARY is None:
        _DICTIONARY = LegalDictionary()
    return _DICTIONARY

def encode_chunks(chunks: List[Dict[str, Any]], show_progress: bool = True) -> np.ndarray:
    """
    Encode chunk texts into an FP16 numpy array.
    Appends synonym definitions to text before embedding.
    """
    if not chunks:
        return np.array([])

    model = _get_model()
    dictionary = _get_dictionary()
    texts = []
    
    for c in chunks:
        base_text = c.get("text", "")
        # Inject synonym if any Latin maxims exist
        enriched_text = dictionary.inject_synonyms(base_text)
        texts.append(enriched_text)

    logger.debug(f"[Embedder] Encoding {len(texts)} chunks...")
    
    embeddings = model.encode(
        texts,
        batch_size=config.EMBEDDING_BATCH,
        show_progress_bar=show_progress,
        convert_to_numpy=True,
        normalize_embeddings=True
    )
    
    # Store and return as fp16 to save memory and network payload
    return embeddings.astype(np.float16)

def encode_query(query: str) -> np.ndarray:
    """
    Encode a single search query, prepending the BGE specific instruction for retrieval.
    Returned as standard float32 for Chroma/FAISS query interface compatibility.
    """
    model = _get_model()
    instruction_query = config.BGE_QUERY_PREFIX + query
    embedding = model.encode(
        instruction_query,
        convert_to_numpy=True,
        normalize_embeddings=True
    )
    return embedding.astype(np.float32)
