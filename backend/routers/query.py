"""
query.py
Router for standard search and streaming QA.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from typing import Any, Dict
import logging

from backend.services.rag_service import rag_service
from backend.services.stream_service import format_sse

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_QUERY_CHARS = 2000
MAX_HISTORY_TURNS = 20


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=MAX_QUERY_CHARS)
    stream: bool = True
    filters: Dict[str, Any] = Field(default_factory=dict)
    use_hyde: bool = True
    use_self_rag: bool = True
    chat_history: list[dict[str, str]] = Field(default_factory=list, max_length=MAX_HISTORY_TURNS)

    @field_validator("query")
    @classmethod
    def query_not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("query must not be blank")
        return cleaned

@router.post("/execute")
async def execute_query(request: QueryRequest):
    """
    Executes the full retrieval pipeline and returns the context chunks.
    This does NOT generate the LLM response itself.
    """
    if not rag_service.initialized:
        raise HTTPException(status_code=503, detail="RAG system not initialized yet.")
        
    try:
        result = await rag_service.query(
            request.query,
            filters=request.filters,
            use_hyde=request.use_hyde,
            use_self_rag=request.use_self_rag,
        )
        return result
    except Exception as e:
        # Internal exception text can carry paths, collection names and config
        # fragments; log it, return an opaque message.
        logger.error("Query execution error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Query execution failed.")

@router.post("/stream")
async def stream_query(request: QueryRequest):
    """
    Executes retrieval and then streams the LLM Answer via SSE.
    """
    if not rag_service.initialized:
        raise HTTPException(status_code=503, detail="RAG system not initialized yet.")
        
    try:
        # Context is ALWAYS retrieved server-side. This endpoint previously
        # accepted a `context_chunks` list from the request body and fed it
        # straight to the LLM as retrieved evidence, which allowed a caller to
        # supply fabricated "case law" and have the model present it as grounded
        # fact. The field has been removed; re-retrieving costs a little latency
        # and removes the forgery vector entirely.
        pipeline_result = await rag_service.query(
            request.query,
            filters=request.filters,
            use_hyde=request.use_hyde,
            use_self_rag=request.use_self_rag,
        )
        context_chunks = pipeline_result.get("context_chunks", [])
        
        # 2. Trigger stream generator
        generator = rag_service.stream_answer(
            request.query,
            context_chunks,
            use_self_rag=request.use_self_rag,
            chat_history=request.chat_history,
        )
        
        # 3. Wrap in SSE and return
        return StreamingResponse(format_sse(generator), media_type="text/event-stream")
        
    except Exception as e:
        logger.error("Stream generation error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Stream generation failed.")
