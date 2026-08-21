"""
Module: parent_retriever.py
Expands semantic search chunks (256 words) to their parent sections (512 words).
Provides "zoomed-in" search with "zoomed-out" context for the LLM.
"""

from typing import List, Dict, Any, Optional

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))
import config

class ParentRetriever:
    def __init__(self):
        self.enabled = config.PARENT_DOC_ENABLED
        
    def expand_to_parents(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        If enabled, replaces the chunk's text with its parent_text.
        """
        if not self.enabled:
            return chunks
            
        expanded_chunks = []
        # Keep track to avoid duplicating the same parent multiple times
        seen_parents = set()
        
        for c in chunks:
            parent_id = c.get("parent_chunk_id")
            parent_text = c.get("parent_text")
            
            # If valid parent mechanism is available
            if parent_id and parent_text:
                if parent_id in seen_parents:
                    continue
                seen_parents.add(parent_id)
                
                # Clone chunk to avoid mutating the original
                new_chunk = dict(c)
                new_chunk["text"] = parent_text
                new_chunk["context_type"] = "parent_expanded"
                expanded_chunks.append(new_chunk)
            else:
                expanded_chunks.append(c)
                
        return expanded_chunks
