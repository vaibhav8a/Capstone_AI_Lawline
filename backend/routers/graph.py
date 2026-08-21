"""
graph.py
Router for Knowledge Graph UI visualization and concept clusters.
"""

from fastapi import APIRouter, HTTPException
import logging

from backend.services.rag_service import rag_service
from backend.modules.concept_clusterer import ConceptClusterer

logger = logging.getLogger(__name__)

router = APIRouter()
clusterer = ConceptClusterer()

@router.get("/lineage/{case_title}")
async def get_case_lineage(case_title: str):
    """
    Returns nodes and edges for React Flow lineage graph.
    """
    if not rag_service.initialized or not rag_service.kg:
        raise HTTPException(status_code=503, detail="KG not loaded.")
        
    try:
        data = rag_service.kg.get_citation_chain(case_title, depth=2)
        return data
    except Exception as e:
        logger.error(f"Graph lineage error: {e}")
        raise HTTPException(status_code=500, detail="Request failed.")

@router.get("/clusters")
async def get_clusters():
    """Returns the legal concept clusters."""
    return clusterer.get_all_clusters()
