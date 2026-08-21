"""
Module: recursive_splitter.py
Hierarchical splitter algorithm:
1. Section boundaries
2. Paragraph markers
3. Sentences
4. Hard caps at 512 for parent and 256 for child chunks.
"""

import re
import hashlib
import logging
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Basic sentence splitter via regex
SENTENCE_RE = re.compile(r'(?<=[.?!])\s+(?=[A-Z])')
# Paragraph splitter via regex (looks for \n\n, ¶, Para N, [N], N.)
PARA_RE = re.compile(r'(?:\n\n+|¶\s*\d+|\bPara(?:graph)?\s+\d+\b|^\s*\d+\.\s|\[\d+\])', re.MULTILINE | re.IGNORECASE)

class RecursiveSplitter:
    def __init__(self, child_cap: int = 256, parent_cap: int = 512, overlap: int = 50):
        # Keep token-like caps as word caps for deterministic chunk sizes.
        self.child_cap_words = max(32, int(child_cap))
        self.parent_cap_words = max(64, int(parent_cap))
        self.overlap_words = max(8, int(overlap))

    def _word_count(self, text: str) -> int:
        return len(text.split())

    def split_document(self, metadata: Dict, doc_pages: List[Dict]) -> List[Dict]:
        """
        Takes raw document pages/sections, returns a list of chunk dicts
        ready for embedding. Each chunk has parent_text, para_numbers, etc.
        """
        all_chunks = []
        
        # doc_pages usually has structure: [{sections: [{section: "NAME", text: "..."}]}]
        for page in doc_pages:
            page_number = page.get("page_number", 0)
            
            for sec in page.get("sections", []):
                sec_name = sec.get("section", "OTHER")
                text = sec.get("text", "")
                if not text.strip(): continue
                
                # Further split the section into parent chunks
                parent_texts = self._split_to_parents(text)
                
                for parent_idx, p_text in enumerate(parent_texts):
                    parent_id = self._stable_id(
                        metadata.get("source_file", metadata.get("case_title", "unknown")),
                        page_number,
                        sec_name,
                        parent_idx,
                        p_text[:200],
                    )
                    
                    # Split parent into children
                    child_texts = self._split_to_children(p_text)
                    
                    for child_idx, c_text in enumerate(child_texts):
                        if not c_text.strip(): continue
                        
                        chunk = {
                            "chunk_id": self._stable_id(parent_id, child_idx, c_text[:160]),
                            "parent_chunk_id": parent_id,
                            "text": c_text.strip(),
                            "parent_text": p_text.strip(),
                            "section": sec_name,
                            "page_number": page_number,
                            "court": metadata.get("court", ""),
                            "case_title": metadata.get("case_title", ""),
                            "date": metadata.get("date", ""),
                            "source_file": metadata.get("source_file", ""),
                            "para_numbers": self._extract_para_numbers(c_text),
                            "scc_page": self._extract_scc_page(c_text),
                            "section_type": self._classify_section_type(sec_name, c_text)
                        }
                        
                        all_chunks.append(chunk)

        # Optional: Resolve citations within this document context here 
        # (or defer to CitationResolver later)
        return all_chunks

    def _stable_id(self, *parts) -> str:
        raw = "::".join(str(p) for p in parts)
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _split_to_parents(self, text: str) -> List[str]:
        """Split text into chunks up to parent_cap_words using paragraphs and sentences."""
        return self._recursive_split(text, self.parent_cap_words, self.overlap_words, use_paras=True)

    def _split_to_children(self, parent_text: str) -> List[str]:
        """Split parent text into chunks up to child_cap_words using sentences."""
        return self._recursive_split(parent_text, self.child_cap_words, self.overlap_words, use_paras=False)

    def _recursive_split(self, text: str, max_words: int, overlap: int, use_paras: bool = False) -> List[str]:
        words = self._word_count(text)
        if words <= max_words:
            return [text]
            
        chunks = []
        splits = []
        
        if use_paras:
            # Try splitting by paragraph
            parts = PARA_RE.split(text)
            if len(parts) > 1:
                splits = [p.strip() for p in parts if p.strip()]
        
        if not splits or len(splits) == 1:
            # Fall back to sentences
            splits = [s.strip() for s in SENTENCE_RE.split(text) if s.strip()]
            
        if not splits or len(splits) == 1:
            # Fall back to word chunks
            word_list = text.split()
            for i in range(0, len(word_list), max_words - overlap):
                chunks.append(" ".join(word_list[i : i + max_words]))
            return chunks

        # Accumulate splits
        current_chunk = []
        current_len = 0
        
        for part in splits:
            part_len = self._word_count(part)
            if current_len + part_len > max_words and current_chunk:
                chunks.append(" ".join(current_chunk))
                # Next chunk starts with overlap
                overlap_words = current_chunk[-overlap:] if len(current_chunk) > overlap else current_chunk
                current_chunk = overlap_words + [part]
                current_len = sum(self._word_count(w) for w in current_chunk) # Approx
            else:
                current_chunk.append(part)
                current_len += part_len
                
        if current_chunk:
            chunks.append(" ".join(current_chunk))
            
        return chunks

    def _extract_para_numbers(self, text: str) -> List[int]:
        import re
        para_re = re.compile(r"(?:¶\s*(\d+)|\bPara(?:graph)?\s+(\d+)\b|^\s*(\d+)\.\s|\[(\d+)\])", re.MULTILINE | re.IGNORECASE)
        found = []
        for m in para_re.findall(text):
            for num in m:
                if num:
                    found.append(int(num))
        return list(set(found))

    def _extract_scc_page(self, text: str) -> Optional[str]:
        import re
        scc_re = re.compile(r"\((\d{4})\)\s*\d+\s*SCC\s*(\d+).*?p(?:age)?[\.:]?\s*(\d+)", re.IGNORECASE)
        match = scc_re.search(text)
        return match.group(3) if match else None

    def _classify_section_type(self, section: str, text: str) -> str:
        sec = section.upper()
        if sec in {"KEY_POINTS", "RATIO", "CONCLUSION"}:
            return "ratio"
        if sec == "REASONING":
            text_lower = text.lower()
            held = [
                "we hold", "it is held", "this court holds", "satisfied that",
                "ratio decidendi", "therefore held", "binding principle", "operative part"
            ]
            obiter = [
                "however", "it may be noted", "observe", "in passing", "need not",
                "for completeness", "obiter", "assuming without deciding"
            ]
            if any(p in text_lower for p in held): return "ratio"
            if any(p in text_lower for p in obiter): return "obiter"
            return "reasoning"
        if sec in {"ORDER", "DECISION"}:
            return "order"
        if sec in {"FACTS", "BACKGROUND", "PREAMBLE"}:
            return "facts"
        return "other"
