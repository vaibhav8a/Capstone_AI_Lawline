"""
summary.py
Router for LLM Case Summarization (Cheat-sheet) and Legal Dictionary.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import logging

from backend.services.rag_service import rag_service
from backend.modules.legal_dictionary import LegalDictionary

logger = logging.getLogger(__name__)
router = APIRouter()
dictionary = LegalDictionary()

class SummaryRequest(BaseModel):
    document_text: str

@router.post("/generate")
async def generate_summary(request: SummaryRequest):
    """Generates the 6-part JSON structured summary."""
    if not rag_service.initialized:
        raise HTTPException(status_code=503, detail="Service not ready.")
        
    try:
        res = await rag_service.get_summary(request.document_text)
        if "error" in res:
            raise HTTPException(status_code=500, detail=res["error"])
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail="Request failed.")

@router.get("/dictionary")
async def get_dictionary():
    """Returns all Latin maxims for the frontend Glossary."""
    return {"maxims": dictionary.get_all_maxims()}
