"""
Module: chroma_store.py
Persistent ChromaDB vector store with true upserts.
Manages multiple collections: all, ratio, facts, citations.
"""

import os
import logging
from typing import List, Dict, Tuple, Set, Optional
import numpy as np

import chromadb
from chromadb.config import Settings

# Depending on script structure, import config.
# If running via backend.main, it might be in root via sys.path
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))
import config

logger = logging.getLogger(__name__)

class ChromaStore:
    def __init__(self, persist_dir: Optional[str] = None):
        self.persist_dir = persist_dir or str(config.CHROMA_PERSIST_PATH)
        os.makedirs(self.persist_dir, exist_ok=True)
        
        logger.info(f"[ChromaStore] Initializing ChromaDB at {self.persist_dir}")
        self.client = chromadb.PersistentClient(
            path=self.persist_dir,
            settings=Settings(anonymized_telemetry=False)
        )
        
        # Initialize collections
        self.collections_names = {
            "all":       config.CHROMA_COLLECTION_ALL,
            "ratio":     config.CHROMA_COLLECTION_RATIO,
            "facts":     config.CHROMA_COLLECTION_FACTS,
            "citations": config.CHROMA_COLLECTION_CITS,
        }
        
        self.collections = {}
        for key, name in self.collections_names.items():
            self.collections[key] = self.client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"} # Use cosine similarity
            )
            
    def get_collection(self, key: str = "all"):
        if key not in self.collections:
            raise ValueError(f"Unknown collection key: {key}")
        return self.collections[key]

    def upsert_chunks(self, chunks: List[Dict], embeddings: np.ndarray, collection_key: str = "all") -> int:
        """Upsert embeddings + metadata into the specified collection."""
        if not chunks:
            return 0
            
        coll = self.get_collection(collection_key)
        
        ids = [c["chunk_id"] for c in chunks]
        documents = [c.get("text", "") for c in chunks]
        
        # Prepare metadata, converting lists to comma-separated strings (Chroma requirement)
        metadatas = []
        for c in chunks:
            meta = {}
            for k, v in c.items():
                if k == "text": continue
                if isinstance(v, (list, dict)):
                    meta[k] = str(v)
                elif v is None:
                    meta[k] = ""
                else:
                    meta[k] = v
            metadatas.append(meta)
            
        # Convert np array to list of lists of floats
        embeddings_list = embeddings.astype(float).tolist()
        
        # Upsert
        coll.upsert(
            ids=ids,
            embeddings=embeddings_list,
            documents=documents,
            metadatas=metadatas
        )
        logger.info(f"[ChromaStore] Upserted {len(chunks)} chunks into collection '{collection_key}'")
        return len(chunks)

    def search(self, query_vec: np.ndarray, top_k: int = 10, collection_key: str = "all", where: Optional[Dict] = None) -> List[Tuple[str, float, Dict]]:
        """Search a collection. Returns (chunk_id, distance, metadata). Note: distance is 1 - cosine_similarity."""
        coll = self.get_collection(collection_key)
        
        query_vec_list = query_vec.astype(float).tolist()
        if len(np.shape(query_vec_list)) == 1:
            query_vec_list = [query_vec_list]
            
        results = coll.query(
            query_embeddings=query_vec_list,
            n_results=top_k,
            where=where
        )
        
        out = []
        if not results["ids"] or not results["ids"][0]:
            return out
            
        for idx in range(len(results["ids"][0])):
            chunk_id = results["ids"][0][idx]
            dist = results["distances"][0][idx]
            meta = results["metadatas"][0][idx] if results.get("metadatas") else {}
            doc = results["documents"][0][idx] if results.get("documents") else ""
            # Reconstruct slightly (distances in Chroma are 1 - cosine if cosine is space)
            # Higher score is better, so 1 - dist
            score = 1.0 - dist
            payload = dict(meta or {})
            if doc:
                payload["text"] = doc
            out.append((chunk_id, score, payload))
            
        return out

    def delete_by_source_file(self, source_file: str, collection_key: str = "all") -> int:
        """Delete all chunks belonging to a source file."""
        if not source_file:
            return 0
        coll = self.get_collection(collection_key)
        res = coll.get(where={"source_file": source_file}, include=[])
        ids = res.get("ids", [])
        if ids:
            coll.delete(ids=ids)
        return len(ids)

    def get_existing_ids(self, collection_key: str = "all") -> Set[str]:
        """Returns the set of chunk IDs existing in the collection."""
        coll = self.get_collection(collection_key)
        # Chroma doesn't have an easy "get all ids" without fetching. 
        # But we can query with include=[] to minimize payload.
        # Note: If > 100k chunks, this might need pagination or offset
        res = coll.get(include=[])
        return set(res["ids"])

    def delete_by_ids(self, chunk_ids: List[str], collection_key: str = "all") -> None:
        """Delete chunks by ID."""
        if not chunk_ids:
            return
        coll = self.get_collection(collection_key)
        coll.delete(ids=chunk_ids)
        logger.info(f"[ChromaStore] Deleted {len(chunk_ids)} chunks from '{collection_key}'")

    def get_collection_stats(self) -> Dict[str, int]:
        """Return count of chunks per collection."""
        stats = {}
        for key, coll in self.collections.items():
            stats[key] = coll.count()
        return stats
