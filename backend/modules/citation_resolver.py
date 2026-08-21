"""
Module: citation_resolver.py
Resolves shortform citations (id., supra, ibid.) to their full forms 
using a sliding window of recent citations.
"""

import re
import logging
from typing import List, Dict, Optional

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))
import config

logger = logging.getLogger(__name__)

class CitationResolver:
    def __init__(self):
        self.window_size = config.CITATION_WINDOW
        
        self.shortform_re = re.compile(r'\b(?:id\.|ibid\.|supra|ante)\b', re.IGNORECASE)
        self.scc_air_re = re.compile(r'(\(\d{4}\)\s*\d+\s*SCC\s*\d+|AIR\s*\d+\s*SC\s*\d+)', re.IGNORECASE)

    def _resolve_shortform(self, label: str, recent_cits: List[str], para_recent: List[str]) -> Optional[str]:
        if label == "ibid":
            return para_recent[-1] if para_recent else (recent_cits[-1] if recent_cits else None)
        if label == "id":
            return recent_cits[-1] if recent_cits else None
        if label == "supra":
            return recent_cits[-2] if len(recent_cits) >= 2 else (recent_cits[-1] if recent_cits else None)
        if label == "ante":
            return recent_cits[0] if recent_cits else None
        return None

    def resolve_document(self, chunks: List[Dict]) -> List[Dict]:
        """
        Process all chunks of a single document IN ORDER.
        Maintains a rolling window of full citations to replace shortforms.
        """
        from .citation_canonicalizer import CitationCanonicalizer
        canonicalizer = CitationCanonicalizer()
        
        # Window stores the canonical/full citations seen recently
        recent_cits = []
        
        for chunk in chunks:
            text = chunk.get("text", "")
            resolved_dict = {}
            para_recent = []
            paragraphs = [p for p in re.split(r"\n\s*\n+", text) if p.strip()]
            
            for para in paragraphs or [text]:
                found = self.scc_air_re.findall(para)
                for f in found:
                    canonical = canonicalizer.resolve_to_canonical(f) or f
                    recent_cits.append(canonical)
                    para_recent.append(canonical)
                    if len(recent_cits) > self.window_size:
                        recent_cits.pop(0)

                for m in self.shortform_re.finditer(para):
                    label = m.group(0).lower().replace(".", "")
                    resolved = self._resolve_shortform(label, recent_cits, para_recent)
                    if resolved:
                        resolved_dict[m.group(0)] = resolved
                    
            chunk["resolved_citations"] = resolved_dict
            
        return chunks
