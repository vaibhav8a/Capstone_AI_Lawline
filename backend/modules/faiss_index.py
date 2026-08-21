"""
Module: faiss_index.py
Fallback/Legacy vector store using FAISS with SQ8 INT8 scalar quantization.
Provides ~4x memory savings.
"""

import pickle
import logging
from pathlib import Path
from typing import List, Tuple, Dict, Any
import numpy as np
import faiss

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))
import config

logger = logging.getLogger(__name__)

class FAISSIndex:
    def __init__(self, index: faiss.Index, chunks: List[Dict[str, Any]]):
        self._index = index
        self._chunks = chunks

    @classmethod
    def build(cls, embeddings: np.ndarray, chunks: List[Dict[str, Any]]) -> "FAISSIndex":
        """
        Builds the FAISS index. If FAISS_USE_SQ8 is true, uses IndexIVFScalarQuantizer.
        """
        logger.info(f"[FAISS] Building index for {len(chunks)} chunks ...")
        
        # Determine if we should use raw float32 or converted fp16
        if embeddings.dtype != np.float32:
            embeddings = embeddings.astype(np.float32)

        n, dim = embeddings.shape
        
        if config.FAISS_USE_SQ8 and n > 256: # SQ8 requires some minimum data to train centroids
            logger.info("[FAISS] Using SQ8 Quantization (IndexIVFScalarQuantizer)...")
            quantizer = faiss.IndexFlatIP(dim)
            nlist = max(4, min(n // 100, 4096))
            index = faiss.IndexIVFScalarQuantizer(
                quantizer, dim, nlist, faiss.ScalarQuantizer.QT_8bit, faiss.METRIC_INNER_PRODUCT
            )
            logger.info(f"[FAISS] Training Voronoi cells (nlist={nlist})...")
            # FAISS training requires float32
            index.train(embeddings)
            index.add(embeddings)
            index.nprobe = min(nlist, 16)
        else:
            logger.info("[FAISS] Using FlatIP (Exact search)...")
            index = faiss.IndexFlatIP(dim)
            index.add(embeddings)

        logger.info(f"[FAISS] Index built. Total vectors: {index.ntotal}")
        return cls(index, chunks)

    def save(self, path: Path = config.FAISS_INDEX_PATH) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        meta_path = path.with_suffix(".meta.pkl")
        
        # Save index
        faiss.write_index(self._index, str(path))
        # Save chunks payload
        with open(meta_path, "wb") as f:
            pickle.dump(self._chunks, f)
            
        logger.info(f"[FAISS] Saved to {path} / {meta_path}")

    @classmethod
    def load(cls, path: Path = config.FAISS_INDEX_PATH) -> "FAISSIndex":
        path = Path(path)
        meta_path = path.with_suffix(".meta.pkl")
        
        if not path.exists() or not meta_path.exists():
            raise FileNotFoundError(f"FAISS index or meta not found: {path}")
            
        index = faiss.read_index(str(path))
        with open(meta_path, "rb") as f:
            chunks = pickle.load(f)
            
        logger.info(f"[FAISS] Loaded index containing {index.ntotal} vectors.")
        return cls(index, chunks)

    def search(self, query_vec: np.ndarray, top_k: int = config.FAISS_TOP_K) -> Tuple[List[int], List[float]]:
        """
        Returns lists of (chunk_indices, scores).
        """
        if query_vec.dtype != np.float32:
            query_vec = query_vec.astype(np.float32)
            
        if len(query_vec.shape) == 1:
            query_vec = query_vec.reshape(1, -1)
            
        scores, idxs = self._index.search(query_vec, top_k)
        
        return idxs[0].tolist(), scores[0].tolist()

    def get_chunk(self, idx: int) -> Dict[str, Any]:
        return self._chunks[idx]
