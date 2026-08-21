"""
statute.py — production endpoints for the IPC/BNS statute assistant.

    GET  /api/statute/corpus     corpus statistics and the IPC/BNS distinction
    POST /api/statute/retrieve   retrieval only, no generation
    POST /api/statute/answer     full grounded pipeline with citations

Security notes
--------------
* Context is **never** accepted from the client. An earlier endpoint in this
  project took a `context_chunks` list from the request body and passed it
  straight into the LLM prompt as retrieved evidence, which let a caller forge
  "case law" and have the model present it as grounded. Context here is only ever
  produced by server-side retrieval.
* Query length is bounded. Unbounded input is both a cost and a prompt-injection
  surface.
* Errors are returned as opaque messages; details go to the server log.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

import config
from backend.services.statute_rag import statute_rag

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_QUERY_CHARS = 2000


class StatuteQuery(BaseModel):
    # Unknown fields are rejected rather than ignored. Pydantic's default is to
    # drop them silently, which means an attempt to smuggle in `context_chunks`
    # would look to the caller like it had been accepted. A 422 makes the refusal
    # explicit and shows up in logs.
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=MAX_QUERY_CHARS)
    # "IPC" | "BNS" | "both" | None. An explicit user choice always wins over
    # the heuristics in corpus_selector.
    corpus: str | None = Field(default=None)
    top_k: int = Field(default=config.STATUTE_TOP_K, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def query_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("query must not be blank")
        return cleaned

    @field_validator("corpus")
    @classmethod
    def corpus_is_known(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalised = value.strip().upper()
        if normalised not in ("IPC", "BNS", "BOTH"):
            raise ValueError("corpus must be one of: IPC, BNS, both")
        return normalised


@router.get("/corpus")
async def corpus_info():
    """Corpus composition, so the UI can state which statutes are searchable."""
    try:
        collection = statute_rag._get_collection()
        everything = collection.get(include=["metadatas"])
        from collections import Counter

        by_law = Counter(m.get("law", "?") for m in everything["metadatas"])
    except Exception as exc:
        logger.error("corpus_info failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail="Statute index is unavailable.")

    return {
        "collection": config.STATUTE_COLLECTION,
        "embedding_model": config.EMBEDDING_MODEL,
        "embedding_dim": config.EMBEDDING_DIM,
        "chunk_strategy": config.STATUTE_CHUNK_STRATEGY,
        "retrieval": "dense",
        "total_sections": sum(by_law.values()),
        "laws": [
            {
                "law": "IPC",
                "name": "The Indian Penal Code, 1860",
                "sections": by_law.get("IPC", 0),
                "status": "repealed",
                "status_note": (
                    "Repealed with effect from 1 July 2024 and replaced by the "
                    "Bharatiya Nyaya Sanhita, 2023. Retained because offences "
                    "committed before that date are still tried under it."
                ),
                "amended_up_to": "1997",
                "currency_warning": (
                    "This consolidation predates the Criminal Law (Amendment) Acts "
                    "of 2013 and 2018. Sections 375, 376 and 376A carry pre-2013 "
                    "text and sections 354A-354D are absent."
                ),
            },
            {
                "law": "BNS",
                "name": "The Bharatiya Nyaya Sanhita, 2023",
                "sections": by_law.get("BNS", 0),
                "status": "in_force",
                "status_note": "In force since 1 July 2024.",
                "amended_up_to": "2023",
                "currency_warning": "",
            },
        ],
        "llm_available": statute_rag.llm_available,
    }


@router.post("/retrieve")
async def retrieve(request: StatuteQuery):
    """Retrieval only — useful for inspecting what the generator would receive."""
    try:
        return statute_rag.retrieve(request.query, request.corpus, request.top_k)
    except RuntimeError as exc:
        logger.error("retrieve unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="Statute index is unavailable.")
    except Exception as exc:
        logger.error("retrieve failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Retrieval failed.")


@router.post("/answer")
async def answer(request: StatuteQuery):
    """Full pipeline: retrieve, ground, generate, cite."""
    try:
        return await statute_rag.answer(request.query, request.corpus, request.top_k)
    except RuntimeError as exc:
        logger.error("answer unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="Statute index is unavailable.")
    except Exception as exc:
        logger.error("answer failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Answer generation failed.")


@router.post("/legal-answer")
async def legal_answer(request: StatuteQuery):
    """Unified statute + judgment pipeline with closed-book generation.

    Returns statutory provisions AND the 3-5 strongest relevant Supreme Court
    judgments, each with citation, date, source URL, a stated relevance reason,
    and the supporting passage.
    """
    from backend.services.legal_rag import legal_rag

    try:
        return await legal_rag.answer(request.query, request.corpus)
    except RuntimeError as exc:
        logger.error("legal-answer unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="Legal index is unavailable.")
    except Exception as exc:
        logger.error("legal-answer failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Answer generation failed.")


@router.post("/legal-retrieve")
async def legal_retrieve(request: StatuteQuery):
    """Retrieval only across both corpora — no generation."""
    from backend.services.legal_rag import legal_rag

    try:
        return legal_rag.retrieve(request.query, request.corpus)
    except RuntimeError as exc:
        logger.error("legal-retrieve unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="Legal index is unavailable.")
    except Exception as exc:
        logger.error("legal-retrieve failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Retrieval failed.")
