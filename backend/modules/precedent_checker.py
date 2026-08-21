"""
Module: precedent_checker.py
Full 5-tier Shepardization + semantic conflict detection.
Incorporates temporal ordering and issue/jurisdiction awareness.
"""

import logging
from typing import List, Dict, Any, Optional
import numpy as np

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))
import config

from .embedder import encode_query
from .knowledge_graph import JURISDICTION_HIERARCHY

logger = logging.getLogger(__name__)

TREATMENT_KEYWORDS = {
    "overruled":     ["overruled", "expressly overruled", "no longer good law", "stands overruled", "bad law"],
    "reversed":      ["reversed", "set aside", "quashed and set aside", "order is set aside"],
    "distinguished": ["distinguished", "distinguishable", "is distinguished", "not applicable to the present case", "facts are different"],
    "per_incuriam":  ["per incuriam", "decided without noticing", "failed to notice", "without reference to"],
    "followed":      ["followed", "approved", "affirmed", "upheld", "relied upon", "consistent with", "in conformity with"],
}

class PrecedentChecker:
    def __init__(self):
        pass
        
    def _parse_year(self, date_str: str) -> int:
        if not date_str: return 0
        try:
            import re
            m = re.search(r'\b(19\d{2}|20\d{2})\b', str(date_str))
            return int(m.group(1)) if m else 0
        except:
            return 0

    def check_temporal_validity(self, target_case: str, chunks: List[Dict]) -> Dict:
        """
        Check all chunks that mention the target case for Shepardization status.
        Sorts referencing cases chronologically (later cases can overrule early ones).
        """
        target_lower = target_case.lower()
        mentions = []
        
        # Filter chunks that explicitly mention the target case (citation)
        for c in chunks:
            text = c.get("text", "").lower()
            if target_lower in text and c.get("case_title", "").lower() != target_lower:
                mentions.append(c)
                
        if not mentions:
            return {"status": "good_law", "confidence": 1.0}
            
        # Chronological sort
        mentions.sort(key=lambda x: self._parse_year(x.get("date", "")), reverse=True)
        
        # Check from newest to oldest
        for chunk in mentions:
            text = chunk.get("text", "").lower()
            year = self._parse_year(chunk.get("date", ""))
            
            for status, keywords in TREATMENT_KEYWORDS.items():
                if any(kw in text for kw in keywords):
                    # Found a treatment
                    # Extract roughly the sentence containing the keyword and target case
                    sentences = [s.strip() for s in text.split(".") if s.strip()]
                    excerpt = " ".join([s for s in sentences if any(kw in s for kw in keywords) or target_lower in s])
                    
                    return {
                        "status": status,
                        "citing_case": chunk.get("case_title"),
                        "treatment_year": year,
                        "excerpt": excerpt if len(excerpt) > 10 else text[:200] + "...",
                        "confidence": 0.95
                    }
                    
        # If mentioned but no specific treatment keywords, assume it's simply cited/good law
        latest = mentions[0]
        return {
            "status": "followed", # Generically treated as positive if cited without negative treatment
            "citing_case": latest.get("case_title"),
            "treatment_year": self._parse_year(latest.get("date", "")),
            "excerpt": latest.get("text", "")[:150] + "...",
            "confidence": 0.60
        }

    def detect_jurisdictional_conflicts(self, chunks: List[Dict], query: str) -> List[Dict]:
        """
        Identifies if two discrete jurisdictions (e.g., Delhi HC vs Bombay HC) 
        have semantically conflicting core holdings on the same issue.
        """
        # 1. Identify "held" sentences per chunk
        held_chunks = []
        held_phrases = [
            "we hold", "held:", "it is held", "the court held", "has held",
            "we are of the view", "in our view", "therefore", "accordingly"
        ]
        
        for c in chunks:
            text_lower = c.get("text", "").lower()
            if any(p in text_lower for p in held_phrases) or c.get("section_type") == "ratio":
                # Find the actual held sentence
                sentences = c.get("text", "").split(".")
                held_sentence = " ".join([s for s in sentences if any(p in s.lower() for p in held_phrases)])
                if not held_sentence:
                    held_sentence = c.get("text", "")[:400]
                    
                court = c.get("court", "").upper().strip()
                if court:
                    held_chunks.append({
                        "court": court,
                        "case_title": c.get("case_title", "Unknown"),
                        "held": held_sentence,
                        "date": c.get("date", ""),
                        "chunk_id": c.get("chunk_id", "")
                    })

        # 2. Group by Court
        court_groups = {}
        for hc in held_chunks:
            court_groups.setdefault(hc["court"], []).append(hc)
            
        conflicts = []
        courts = list(court_groups.keys())
        
        # We need at least 2 distinct courts
        if len(courts) < 2:
            return conflicts
            
        # 3. Compare representations
        import numpy as np
        
        # Flatten all into a list to encode
        all_held = []
        mapping = {}
        idx = 0
        for court, items in court_groups.items():
            for item in items:
                all_held.append(item["held"])
                mapping[idx] = item
                idx += 1
                
        # To avoid circular imports doing encode, we use the local encode_query helper or just mock sim
        encoded = np.array([encode_query(h) for h in all_held])
        
        # Compute cosine similarity
        for i in range(len(encoded)):
            for j in range(i + 1, len(encoded)):
                item_i = mapping[i]
                item_j = mapping[j]
                
                # Only compare distinct courts
                if item_i["court"] == item_j["court"]:
                    continue
                    
                vec1 = encoded[i]
                vec2 = encoded[j]
                
                sim = np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))
                
                # Check jurisdictional override
                # If one court binds another (e.g. SC binds HC), it's not a conflict, it's an overruling
                c1 = item_i["court"]
                c2 = item_j["court"]
                
                is_override = False
                c1_level = JURISDICTION_HIERARCHY.get(c1, {}).get("level", 3)
                c2_level = JURISDICTION_HIERARCHY.get(c2, {}).get("level", 3)
                
                if c1_level != c2_level:
                    # One is higher. No horizontal conflict, it's vertical settling of law.
                    is_override = True

                # If opposing legal polarity words are present, treat as stronger conflict signal.
                text_pair = f"{item_i['held']} {item_j['held']}".lower()
                opposite_polarity = (
                    ("allowed" in text_pair and "dismissed" in text_pair)
                    or ("valid" in text_pair and "invalid" in text_pair)
                    or ("constitutional" in text_pair and "unconstitutional" in text_pair)
                    or ("liable" in text_pair and "not liable" in text_pair)
                )

                # If low semantic similarity or explicit opposite polarity, and parallel courts
                if (sim < 0.60 or opposite_polarity) and not is_override:
                    conflicts.append({
                        "court_a": c1,
                        "court_b": c2,
                        "case_a": item_i["case_title"],
                        "case_b": item_j["case_title"],
                        "held_a": item_i["held"],
                        "held_b": item_j["held"],
                        "similarity": float(sim),
                        "conflict_topic": query
                    })
                    
        # Sort by most conflicting (lowest similarity first)
        conflicts.sort(key=lambda x: x["similarity"])
        
        # Deduplicate
        unique_conflicts = []
        seen = set()
        for c in conflicts:
            key = tuple(sorted([c["case_a"], c["case_b"]]))
            if key not in seen:
                seen.add(key)
                unique_conflicts.append(c)
                
        return unique_conflicts
