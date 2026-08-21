"""
delta_worker.py
RQ Worker task that runs delta indexing on newly processed JSON files.
Called by the indexing service or watchdog.
"""

import os
import sys
import json
import logging
from pathlib import Path

# Add project root to sys path so we can import from backend.*
sys.path.append(str(Path(__file__).parent.parent.parent))
from backend.modules.chunk_manager import build_chunks
from backend.modules.embedder import encode_chunks
from backend.modules.chroma_store import ChromaStore
from backend.modules.knowledge_graph import KnowledgeGraph
import config

logger = logging.getLogger(__name__)


def _mark_indexed(json_path: str, document_hash: str):
    if not document_hash:
        return
    manifest_path = Path(config.OUTPUT_DIR) / "index_manifest.json"
    manifest = {}
    if manifest_path.exists():
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            manifest = {}
    manifest[Path(json_path).name] = document_hash
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

def process_delta_index(json_path: str):
    """
    Reads a processed JSON document.
    Chunks, embeds, and upserts it into ChromaDB.
    Updates KnowledgeGraph.
    """
    logger.info(f"[DeltaWorker] Beginning delta index for {json_path}")
    
    try:
        with open(json_path, 'r') as f:
            doc_data = json.load(f)
        doc_hash = doc_data.get("document_hash")
        source_file = doc_data.get("file_name", "")
            
        # 1. Chunk and enrich
        chunks = build_chunks([doc_data])
        if not chunks:
            logger.info(f"[DeltaWorker] No chunks generated for {json_path}")
            return
            
        # 2. Embed
        embeddings = encode_chunks(chunks, show_progress=False)
        
        # 3. Upsert to ChromaDB
        store = ChromaStore()
        if source_file:
            # Purge prior versions from all collections, then upsert fresh chunks.
            for collection in ("all", "ratio", "facts", "citations"):
                store.delete_by_source_file(source_file, collection)
        
        # Route to different collections based on section mapping (very basic example)
        ratio_chunks = [c for c in chunks if c.get('section_type') == 'ratio']
        facts_chunks = [c for c in chunks if c.get('section_type') == 'facts']
        citation_chunks = [c for c in chunks if c.get("resolved_citations") or c.get("citations")]
        
        store.upsert_chunks(chunks, embeddings, "all")
        
        if ratio_chunks:
            r_embeds = encode_chunks(ratio_chunks, show_progress=False)
            store.upsert_chunks(ratio_chunks, r_embeds, "ratio")
            
        if facts_chunks:
            f_embeds = encode_chunks(facts_chunks, show_progress=False)
            store.upsert_chunks(facts_chunks, f_embeds, "facts")

        if citation_chunks:
            c_embeds = encode_chunks(citation_chunks, show_progress=False)
            store.upsert_chunks(citation_chunks, c_embeds, "citations")
            
        # 4. Update Knowledge Graph
        try:
            kg = KnowledgeGraph.load()
            kg.merge_chunks(chunks)
        except Exception:
            kg = KnowledgeGraph.build(chunks)
            
        kg.save()
        _mark_indexed(json_path, doc_hash)
            
        logger.info(f"[DeltaWorker] Successfully indexed {len(chunks)} chunks for {json_path}")
        return {"status": "success", "chunks_added": len(chunks)}
        
    except Exception as e:
        logger.error(f"[DeltaWorker] Failed to index {json_path}: {e}")
        return {"status": "error", "message": str(e)}
