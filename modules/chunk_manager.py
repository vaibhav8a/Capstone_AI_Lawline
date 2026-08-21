"""
Module 2 — Chunk Manager
Validates chunks and enriches each one with document-level metadata.
"""

import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


def _resolve_section(chunk: Dict[str, Any], sections: List[Any]) -> str:
    """
    Map the chunk's section reference to a human-readable section title.
    Handles both list-of-strings and list-of-dicts formats.
    """
    # If the chunk already carries a section tag, respect it
    if chunk.get("section"):
        return str(chunk["section"])

    # Try to derive from the flat sections list
    if sections:
        first = sections[0]
        if isinstance(first, dict):
            return first.get("title", first.get("heading", "Unknown"))
        return str(first)

    return "Unknown"


def _extract_metadata(meta: Any) -> Dict[str, str]:
    """Safely pull case_title, court from the metadata blob (dict or str)."""
    if isinstance(meta, dict):
        title = (
            meta.get("case_title")
            or meta.get("title")
            or meta.get("case_name")
            or "Unknown"
        )
        court = meta.get("court") or meta.get("court_name") or "Unknown"
        date  = meta.get("date") or meta.get("judgment_date") or ""
    else:
        title = str(meta) if meta else "Unknown"
        court = "Unknown"
        date  = ""
    return {"case_title": title, "court": court, "date": date}


def build_chunks(documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Flatten and enrich all chunks across all documents.

    For each chunk the following fields are guaranteed:
        - text         : str   — the chunk text
        - chunk_id     : str   — globally unique ID  "<doc_idx>_<chunk_idx>"
        - doc_id       : int
        - section      : str
        - case_title   : str
        - court        : str
        - date         : str
        - citations    : list[str]
        - source_file  : str
    """
    all_chunks: List[Dict[str, Any]] = []
    skipped = 0

    for doc_idx, doc in enumerate(documents):
        raw_chunks  = doc.get("chunks", [])
        sections    = doc.get("sections", [])
        citations   = doc.get("citations", [])
        meta_info   = _extract_metadata(doc.get("metadata", {}))
        source_file = doc.get("_source_file", "")

        # Normalise citations to a flat list of strings
        citation_list: List[str] = []
        for c in citations:
            if isinstance(c, dict):
                citation_list.append(
                    c.get("citation") or c.get("text") or c.get("id") or str(c)
                )
            elif isinstance(c, str):
                citation_list.append(c)

        for chunk_idx, chunk in enumerate(raw_chunks):
            # Accept both string chunks and dict chunks
            if isinstance(chunk, str):
                chunk = {"text": chunk}

            text = chunk.get("text", "").strip()
            if not text:
                logger.debug(
                    f"[ChunkManager] Skipping empty chunk "
                    f"doc={doc_idx} chunk={chunk_idx}"
                )
                skipped += 1
                continue

            enriched = {
                # Core content
                "text": text,
                # Identity
                "chunk_id":   f"{doc_idx}_{chunk_idx}",
                "doc_id":     doc_idx,
                "chunk_idx":  chunk_idx,
                # Structural metadata
                "section":    _resolve_section(chunk, sections),
                # Document-level metadata
                "case_title": meta_info["case_title"],
                "court":      meta_info["court"],
                "date":       meta_info["date"],
                "citations":  citation_list,
                "source_file": source_file,
            }

            # Preserve any extra keys already present in the chunk
            for k, v in chunk.items():
                if k not in enriched:
                    enriched[k] = v

            all_chunks.append(enriched)

    logger.info(
        f"[ChunkManager] Built {len(all_chunks)} enriched chunks "
        f"({skipped} empty chunks skipped) from {len(documents)} documents"
    )
    return all_chunks
