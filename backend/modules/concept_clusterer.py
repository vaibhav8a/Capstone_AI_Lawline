"""
Module: concept_clusterer.py
Continuous background matching using scikit-learn or rules to map
diverse keywords to unified semantic anchor topics.
"""

from typing import List, Dict, Optional

class ConceptClusterer:
    def __init__(self):
        # A static mapping for the demo, but can be scaled using MiniLM or TF-IDF.
        self.anchor_topics = {
            "Article 21": ["personal liberty", "right to life", "privacy", "due process"],
            "Basic Structure Doctrine": ["constitutional amendment", "kesavananda", "judicial review power", "secularism feature"],
            "Strict Liability": ["absolute liability", "hazardous substance", "m c mehta", "oleum gas leak"]
        }

    def map_to_cluster(self, text_keywords: List[str] | str) -> List[str]:
        """
        Maps a list of extracted keywords or text fragment into predefined anchor topics.
        """
        if not text_keywords:
            return []
            
        if isinstance(text_keywords, str):
            text_keywords = [text_keywords]
            
        clusters = set()
        
        for keyword in text_keywords:
            kw_lower = keyword.lower()
            for anchor, synonyms in self.anchor_topics.items():
                if kw_lower == anchor.lower():
                    clusters.add(anchor)
                    continue
                    
                for syn in synonyms:
                    if syn in kw_lower or kw_lower in syn:
                        clusters.add(anchor)
                        
        return list(clusters)
        
    def get_all_clusters(self) -> Dict[str, List[str]]:
        return self.anchor_topics
