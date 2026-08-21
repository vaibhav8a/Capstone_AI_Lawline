"""
Module 1 — JSON Loader
Discovers and loads all pre-processed JSON legal documents from a folder.
Handles both flat format (chunks at top level) and preprocessor format
(chunks nested inside pages → sections → chunks).
"""

import json
import logging
from pathlib import Path
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

REQUIRED_KEYS = {"metadata", "chunks"}
OPTIONAL_KEYS = {"sections", "entities", "citations"}


def _validate_document(doc: Dict[str, Any], filepath: Path) -> bool:
    """Return True if the document has the minimum required structure."""
    missing = REQUIRED_KEYS - doc.keys()
    if missing:
        logger.warning(f"[JSONLoader] Skipping {filepath.name} — missing keys: {missing}")
        return False
    if not isinstance(doc.get("chunks"), list) or len(doc["chunks"]) == 0:
        logger.warning(f"[JSONLoader] Skipping {filepath.name} — 'chunks' is empty or not a list")
        return False
    return True


def _normalise(raw: Dict[str, Any], filepath: Path) -> Dict[str, Any]:
    """
    Convert preprocessor-output format into flat pipeline format.

    Preprocessor schema:
      pages[n].sections[n].chunks[n].text  →  flat chunks list

    Pipeline schema:
      { metadata, chunks: [{text, section, page_number}], sections, citations, entities }
    """
    # Flat format: non-empty top-level chunks (do not skip nested pages if chunks is empty)
    if (
        "chunks" in raw
        and isinstance(raw.get("chunks"), list)
        and len(raw["chunks"]) > 0
    ):
        return raw

    chunks: List[Dict[str, Any]] = []
    section_names: List[str]     = []
    all_citations: List[Any]     = list(raw.get("global_citations", []))
    all_entities: List[Any]      = []

    for page in raw.get("pages", []):
        page_num = page.get("page_number", 0)
        for sec in page.get("sections", []):
            sec_name = sec.get("section", "Unknown")
            if sec_name and sec_name not in section_names:
                section_names.append(sec_name)

            # Citations
            for cit in sec.get("citations", []) + sec.get("case_citations", []):
                if cit and cit not in all_citations:
                    all_citations.append(cit)

            # Entities
            ents = sec.get("entities", {})
            if isinstance(ents, dict):
                for s in ents.get("statutes", []):
                    all_entities.append({"type": "ACT", "text": s})
                for c in ents.get("case_names", []):
                    all_entities.append({"type": "CASE_REF", "text": c})
            elif isinstance(ents, list):
                all_entities.extend(ents)

            # Chunks
            for chunk in sec.get("chunks", []):
                if isinstance(chunk, str):
                    chunk = {"text": chunk}
                text = chunk.get("text", "").strip()
                if text:
                    chunks.append({
                        "text":        text,
                        "section":     sec_name,
                        "page_number": page_num,
                        "chunk_id":    chunk.get("chunk_id"),
                    })

    # Normalise metadata — strip newlines inserted by OCR
    meta_raw = raw.get("metadata", {})
    if isinstance(meta_raw, dict):
        meta = {
            k: str(v).replace("\n", " ").strip() if isinstance(v, str) else v
            for k, v in meta_raw.items()
        }
    else:
        meta = {"case_title": str(meta_raw)}

    return {
        "metadata":  meta,
        "chunks":    chunks,
        "sections":  section_names,
        "citations": all_citations,
        "entities":  all_entities,
        "file_name": raw.get("file_name", filepath.name),
    }


def load_documents(input_folder: str) -> List[Dict[str, Any]]:
    """
    Recursively load all *.json files from *input_folder*.

    Returns
    -------
    List of validated, normalised document dicts guaranteed to have
    'metadata' and non-empty 'chunks'.
    """
    folder = Path(input_folder)
    if not folder.exists():
        raise FileNotFoundError(f"Input folder not found: {folder}")
    if not folder.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {folder}")

    json_files = sorted(folder.rglob("*.json"))
    if not json_files:
        raise ValueError(f"No *.json files found under: {folder}")

    logger.info(f"[JSONLoader] Found {len(json_files)} JSON file(s) in '{folder}'")

    documents: List[Dict[str, Any]] = []
    errors = 0

    for filepath in json_files:
        try:
            with open(filepath, "r", encoding="utf-8") as fh:
                raw = json.load(fh)

            doc = _normalise(raw, filepath)
            doc["_source_file"] = str(filepath)

            # Ensure optional keys exist
            for key in OPTIONAL_KEYS:
                doc.setdefault(key, [])

            if _validate_document(doc, filepath):
                documents.append(doc)
            else:
                errors += 1

        except json.JSONDecodeError as exc:
            logger.error(f"[JSONLoader] JSON parse error in {filepath.name}: {exc}")
            errors += 1
        except OSError as exc:
            logger.error(f"[JSONLoader] Cannot read {filepath.name}: {exc}")
            errors += 1

    logger.info(
        f"[JSONLoader] Loaded {len(documents)} valid document(s) "
        f"({errors} skipped due to errors)"
    )
    return documents
