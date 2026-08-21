"""
compare.py
Router to handle semantic conflict detection between queries/cases.
Enhanced to provide meaningful legal analysis for law students.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import logging
from typing import List, Dict, Any

from backend.services.rag_service import rag_service

logger = logging.getLogger(__name__)

router = APIRouter()

class ConflictRequest(BaseModel):
    query: str
    
class ComparisonRequest(BaseModel):
    case1_title: str
    case2_title: str
    query: str = ""

@router.post("/detect")
async def detect_conflicts(request: ConflictRequest):
    """
    Enhanced Jurisdictional Conflict Detection.

    Uses multi-query retrieval to ensure both supporting
    and opposing legal doctrines are retrieved.
    """

    if not rag_service.initialized:
        raise HTTPException(
            status_code=503,
            detail="Service not ready."
        )

    try:

        logger.info(
            f"Starting conflict detection for: {request.query}"
        )

        # ----------------------------------
        # STEP 1 — Expand Conflict Queries
        # ----------------------------------

        expanded_queries = [
            request.query,
             f"cases opposing {request.query}",
             f"exceptions to {request.query}"
        ]

        logger.info(
            f"Expanded queries: {expanded_queries}"
        )

        # ----------------------------------
        # STEP 2 — Retrieve Context
        # ----------------------------------

        all_chunks = []

        for q in expanded_queries:

            res = await rag_service.query(q)

            chunks = res.get(
                "context_chunks",
                []
            )

            all_chunks.extend(chunks)

        # ----------------------------------
        # STEP 3 — Remove Duplicates
        # ----------------------------------

        unique_chunks = []

        seen_texts = set()

        for c in all_chunks:

            text = c.get("text", "")

            if text and text not in seen_texts:

                seen_texts.add(text)

                unique_chunks.append(c)

        logger.info(
            f"Total unique chunks: {len(unique_chunks)}"
        )

        # Limit context size
        chunks = unique_chunks[:10]

        # ----------------------------------
        # STEP 4 — Run Conflict Detection
        # ----------------------------------

        conflicts = rag_service.check_conflicts(
            request.query,
            chunks
        )

        logger.info(
            f"Conflicts detected: {len(conflicts)}"
        )

        # ----------------------------------
        # STEP 5 — Group by Jurisdiction
        # ----------------------------------

        by_jurisdiction = {}

        for conflict in conflicts:

            court = conflict.get(
                "court",
                "Unknown"
            )

            if court not in by_jurisdiction:

                by_jurisdiction[court] = []

            by_jurisdiction[court].append(conflict)

        # ----------------------------------
        # STEP 6 — Generate Summary
        # ----------------------------------

        summary = _generate_conflict_summary(
            by_jurisdiction
        )

        return {

            "conflicts": conflicts,

            "by_jurisdiction": by_jurisdiction,

            "total_conflicts": len(conflicts),

            "summary": summary

        }

    except Exception as e:

        logger.error(
            f"Conflict detection error: {e}"
        )

        raise HTTPException(
            status_code=500,
            detail="Request failed."
        )

def _generate_conflict_summary(
    by_jurisdiction: Dict[str, List[Dict]]
) -> str:

    """
    Generate readable conflict summary.
    """

    if not by_jurisdiction:

        return (
            "No direct conflicts detected. "
            "However, doctrinal differences may still exist."
        )

    summary_parts = []

    for court, conflicts in by_jurisdiction.items():

        if conflicts:

            summary_parts.append(
                f"{court}: {len(conflicts)} conflicting positions"
            )

    if summary_parts:

        return " | ".join(summary_parts)

    return (
        "No direct conflicts detected. "
        "Further doctrinal analysis recommended."
    )

class TimelineRequest(BaseModel):
    query: str

@router.post("/timeline")
async def get_timeline(request: TimelineRequest):
    """
    Retrieves cases on the query topic and sorts them chronologically
    to visualize the evolution of the law. Enhanced for educational use.
    """
    if not rag_service.initialized:
        raise HTTPException(status_code=503, detail="Service not ready.")
    
    try:
        # 1. Retrieve Context
        res = await rag_service.query(request.query)
        chunks = res.get("context_chunks", [])
        
        # 2. Extract and format Timeline Node Data
        timeline_nodes = []
        seen_cases = set()
        
        for c in chunks:
            case_name = c.get("case_title")
            if case_name and case_name not in seen_cases:
                seen_cases.add(case_name)
                
                # attempt date parse
                date_str = c.get("date", "")
                year = 0
                import re
                m = re.search(r'\b(19\d{2}|20\d{2})\b', str(date_str))
                if m:
                    year = int(m.group(1))
                
                # Extract key legal principles for law students
                text = c.get("text", "")
                key_principles = _extract_key_principles(text)
                
                timeline_nodes.append({
                    "case": case_name,
                    "date": date_str,
                    "year": year,
                    "court": c.get("court", ""),
                    "excerpt": c.get("text", "")[:200] + "...",
                    "section_type": c.get("section_type", ""),
                    "key_principles": key_principles,
                    "chunk_id": c.get("chunk_id", ""),
                })
        
        # 3. Sort chronologically
        timeline_nodes.sort(key=lambda x: x["year"] if x["year"] else 9999)
        
        return {
            "timeline": timeline_nodes,
            "total_cases": len(seen_cases),
            "span": {
                "earliest": timeline_nodes[0]["year"] if timeline_nodes else None,
                "latest": timeline_nodes[-1]["year"] if timeline_nodes else None,
            }
        }
        
    except Exception as e:
        logger.error(f"Timeline generation error: {e}")
        raise HTTPException(status_code=500, detail="Request failed.")

def _extract_key_principles(text: str) -> List[str]:
    """Extract key legal principles from text for student learning."""
    principles = []
    keywords = [
        "held that", "principle", "established", "law", "right", 
        "duty", "obligation", "liability", "doctrine", "test",
        "ratio decidendi", "held"
    ]
    
    text_lower = text.lower()
    for keyword in keywords:
        if keyword in text_lower:
            # Extract sentences containing the keyword
            sentences = text.split('.')
            for sentence in sentences:
                if keyword in sentence.lower() and len(sentence.strip()) > 20:
                    principles.append(sentence.strip()[:100] + "...")
                    break
            if len(principles) >= 3:
                break
    
    return principles[:3]

@router.post("/compare")
async def compare_cases(request: ComparisonRequest):
    """
    Compare two cases side-by-side to show differences and similarities.
    Useful for understanding jurisprudential evolution.
    """
    if not rag_service.initialized:
        raise HTTPException(status_code=503, detail="Service not ready.")
    
    try:
        # Search for both cases
        query1 = f"facts holding and ratio of {request.case1_title}"
        query2 = f"facts holding and ratio of {request.case2_title}"
        
        res1 = await rag_service.query(query1)
        res2 = await rag_service.query(query2)
        
        chunks1 = res1.get("context_chunks", [])[:3]  # Top 3 chunks
        chunks2 = res2.get("context_chunks", [])[:3]
        
        # Extract comparison points
        comparison = {
            "case1": {
                "title": request.case1_title,
                "chunks": chunks1,
                "facts": _extract_section(chunks1, "facts"),
                "ratio": _extract_section(chunks1, "ratio"),
                "holding": _extract_section(chunks1, "holding"),
                "year": _extract_year(chunks1),
                "court": _extract_court(chunks1),
            },
            "case2": {
                "title": request.case2_title,
                "chunks": chunks2,
                "facts": _extract_section(chunks2, "facts"),
                "ratio": _extract_section(chunks2, "ratio"),
                "holding": _extract_section(chunks2, "holding"),
                "year": _extract_year(chunks2),
                "court": _extract_court(chunks2),
            },
            "analysis": _generate_comparison_analysis(chunks1, chunks2, request.case1_title, request.case2_title)
        }
        
        return comparison
    except Exception as e:
        logger.error(f"Case comparison error: {e}")
        raise HTTPException(status_code=500, detail="Request failed.")

def _extract_section(chunks: List[Dict], section_type: str) -> str:
    """Extract a specific section from chunks."""
    for chunk in chunks:
        if chunk.get("section_type", "").lower() == section_type.lower():
            return chunk.get("text", "")[:300] + "..."
    # If specific section not found, return first chunk
    return chunks[0].get("text", "")[:300] + "..." if chunks else ""

def _extract_year(chunks: List[Dict]) -> int:
    """Extract year from chunks."""
    for chunk in chunks:
        import re
        date_str = str(chunk.get("date", ""))
        m = re.search(r'\b(19\d{2}|20\d{2})\b', date_str)
        if m:
            return int(m.group(1))
    return 0

def _extract_court(chunks: List[Dict]) -> str:
    """Extract court name from chunks."""
    for chunk in chunks:
        court = chunk.get("court")
        if court:
            return court
    return "Unknown"

def _generate_comparison_analysis(chunks1: List[Dict], chunks2: List[Dict], case1: str, case2: str) -> Dict[str, Any]:
    """Generate comparative analysis for law students."""
    return {
        "similarities": _find_similarities(chunks1, chunks2),
        "differences": _find_differences(chunks1, chunks2),
        "precedential_relationship": _analyze_precedent_relationship(chunks1, chunks2),
        "learning_points": _extract_learning_points(chunks1, chunks2, case1, case2),
    }

def _find_similarities(chunks1: List[Dict], chunks2: List[Dict]) -> List[str]:
    """Find similarities between two case sets."""
    similarities = []
    texts1 = [c.get("text", "").lower() for c in chunks1]
    texts2 = [c.get("text", "").lower() for c in chunks2]
    
    # Find common legal concepts
    concepts = ["right", "duty", "liability", "damages", "relief", "decree", "justice"]
    for concept in concepts:
        if any(concept in t for t in texts1) and any(concept in t for t in texts2):
            similarities.append(f"Both cases address the concept of '{concept}'")
    
    return similarities[:3]

def _find_differences(chunks1: List[Dict], chunks2: List[Dict]) -> List[str]:
    """Find key differences between two case sets."""
    differences = []
    
    year1 = _extract_year(chunks1)
    year2 = _extract_year(chunks2)
    if year1 and year2:
        differences.append(f"Cases are {abs(year1-year2)} years apart ({year1} vs {year2})")
    
    court1 = _extract_court(chunks1)
    court2 = _extract_court(chunks2)
    if court1 != court2:
        differences.append(f"Different courts: {court1} vs {court2}")
    
    return differences

def _analyze_precedent_relationship(chunks1: List[Dict], chunks2: List[Dict]) -> str:
    """Analyze if one case follows, overrules, or distinguishes from the other."""
    # Check for citation references
    texts = [c.get("text", "").lower() for c in chunks1 + chunks2]
    all_text = " ".join(texts)
    
    if any(word in all_text for word in ["followed", "approved", "relied upon", "consistent with"]):
        return "May have a 'followed' relationship"
    elif any(word in all_text for word in ["distinguished", "distinguishable", "differs from"]):
        return "May have a 'distinguished' relationship"
    elif any(word in all_text for word in ["overruled", "reversed", "set aside"]):
        return "May have an 'overruled' relationship"
    else:
        return "Relationship unclear - may be independent authorities"

def _extract_learning_points(chunks1: List[Dict], chunks2: List[Dict], case1: str, case2: str) -> List[str]:
    """Extract key learning points for law students."""
    points = []
    points.append(f"Compare how {case1} and {case2} handle similar legal issues")
    points.append("Identify which precedent is binding in your jurisdiction")
    points.append("Note the evolution of legal principles over time")
    points.append("Consider how courts adapt precedents to new circumstances")
    return points


