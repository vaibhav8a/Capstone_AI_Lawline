"""
eval_router.py — API endpoint to trigger evaluation runs
"""

from fastapi import APIRouter, HTTPException
from backend.services.rag_service import rag_service
from evaluation.metrics import run_evaluation

router = APIRouter()

@router.post("/run")
async def run_eval():
    """Runs the full gold query evaluation suite."""
    if not rag_service.initialized:
        raise HTTPException(status_code=503, detail="RAG service not ready")
    try:
        summary = await run_evaluation(rag_service)
        return summary
    except Exception as e:
        raise HTTPException(status_code=500, detail="Request failed.")
