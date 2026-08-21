"""
Module: chunk_manager.py
Enriches documents with metadata, applies recursive splitting, 
and resolves citations.
"""

import logging
from typing import List, Dict, Any
from .recursive_splitter import RecursiveSplitter
from .citation_resolver import CitationResolver

logger = logging.getLogger(__name__)

def _extract_metadata(meta: Any) -> Dict[str, str]:
    if isinstance(meta, dict):
        title = meta.get("case_title") or meta.get("title") or meta.get("case_name") or "Unknown"
        court = meta.get("court") or meta.get("court_name") or "Unknown"
        date  = meta.get("date") or meta.get("judgment_date") or ""
        source_file = meta.get("source_file") or meta.get("file_name") or ""
    else:
        title = str(meta) if meta else "Unknown"
        court = "Unknown"
        date  = ""
        source_file = ""
    return {"case_title": title, "court": court, "date": date, "source_file": source_file}

def build_chunks(documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Process raw documents, apply recursive splitter, extract point-cites,
    and resolve citations.
    """
    all_chunks = []
    skipped_docs = 0
    
    splitter = RecursiveSplitter()
    resolver = CitationResolver()

    for doc_idx, doc in enumerate(documents):
        pages = doc.get("pages", [])
        if not pages:
            # Fallback if the document doesn't have the standard pages structure
            chunks = doc.get("chunks", [])
            if chunks:
                logger.warning(f"[ChunkManager] Document {doc_idx} has no 'pages'. Using fallback chunks.")
                # Basic enrichment for flat chunks
                meta = _extract_metadata(doc.get("metadata", {}))
                for idx, c in enumerate(chunks):
                    text = c.get("text", "") if isinstance(c, dict) else str(c)
                    if not text.strip(): continue
                    all_chunks.append({
                        "chunk_id": f"{doc_idx}_{idx}_fallback",
                        "text": text,
                        "parent_text": text, # No parent for fallback
                        "case_title": meta["case_title"],
                        "court": meta["court"],
                        "date": meta["date"],
                        "section": "Unknown",
                        "section_type": "other"
                    })
            else:
                skipped_docs += 1
            continue

        meta_info = _extract_metadata(doc.get("metadata", {}))
        if "source_file" not in meta_info or not meta_info["source_file"]:
            meta_info["source_file"] = doc.get("file_name", "")
        
        # 1. Split into enriched chunks
        doc_chunks = splitter.split_document(meta_info, pages)
        
        # 2. Resolve shortform citations
        doc_chunks = resolver.resolve_document(doc_chunks)
        
        # 3. Add to pool
        all_chunks.extend(doc_chunks)

    logger.info(
        f"[ChunkManager] Built {len(all_chunks)} chunks using recursive splitting "
        f"from {len(documents)} documents (skipped {skipped_docs})."
    )
    return all_chunks
