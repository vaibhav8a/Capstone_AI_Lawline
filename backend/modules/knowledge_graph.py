"""
Module: knowledge_graph.py
Builds a directed legal knowledge graph using NetworkX.
Includes granular Act->Section->Case edges and jurisdiction hierarchies.
"""

import logging
import pickle
import re
from pathlib import Path
from typing import List, Dict, Any, Set, Tuple

import networkx as nx

import sys
sys.path.append(str(Path(__file__).parent.parent.parent))
import config

logger = logging.getLogger(__name__)

# Basic jurisdiction mapping for courts
JURISDICTION_HIERARCHY = {
    "SUPREME COURT": {"level": 1, "persuades": ["ALL"]},
    "DELHI HIGH COURT": {"level": 2, "persuades": ["BOMBAY HIGH COURT", "MADRAS HIGH COURT"]},
    "BOMBAY HIGH COURT": {"level": 2, "persuades": ["DELHI HIGH COURT", "MADRAS HIGH COURT"]},
    "MADRAS HIGH COURT": {"level": 2, "persuades": ["DELHI HIGH COURT", "BOMBAY HIGH COURT"]}
}

def _node_id(node_type: str, label: str) -> str:
    return f"{node_type}::{label.strip().lower()}"

def _add_node(G: nx.DiGraph, node_type: str, label: str, **attrs) -> str:
    nid = _node_id(node_type, label)
    if not G.has_node(nid):
        G.add_node(nid, type=node_type, label=label.strip(), **attrs)
    return nid

def _add_edge(G: nx.DiGraph, src: str, dst: str, relation: str, **attrs) -> None:
    if not G.has_edge(src, dst):
        G.add_edge(src, dst, relation=relation, **attrs)

class KnowledgeGraph:
    def __init__(self, graph: nx.DiGraph, chunks: List[Dict[str, Any]]):
        self._G = graph
        self._chunks = chunks

    @classmethod
    def build(cls, chunks: List[Dict[str, Any]]) -> "KnowledgeGraph":
        """
        Builds graph from enriched chunks.
        Extracts Acts -> Sections, citations, and jurisdiction relations.
        """
        G = nx.DiGraph()
        
        # Build jurisdiction baseline
        sc_id = _add_node(G, "Court", "SUPREME COURT OF INDIA")
        for court, data in JURISDICTION_HIERARCHY.items():
            if data["level"] == 2:
                hc_id = _add_node(G, "Court", court)
                _add_edge(G, sc_id, hc_id, "binds")
        
        # Regex to catch "Section XYZ of the ABC Act"
        section_act_re = re.compile(r"Section\s+([\w\.]+)\s+of\s+(?:the\s+)?([A-Za-z\s]+Act)", re.IGNORECASE)

        for chunk in chunks:
            case_label = chunk.get("case_title", "Unknown")
            court_label = chunk.get("court", "Unknown Court")
            date = chunk.get("date", "")
            
            case_id = _add_node(G, "Case", case_label, date=date)
            court_id = _add_node(G, "Court", court_label)
            _add_edge(G, case_id, court_id, "decided_by")
            
            # Resolved citations (from CitationResolver)
            resolved = chunk.get("resolved_citations", {})
            for original, resolved_case in resolved.items():
                if resolved_case:
                    cit_id = _add_node(G, "Case", resolved_case)
                    _add_edge(G, case_id, cit_id, "cites_resolved", citation=original)

            # Raw citations fallback
            for cit in chunk.get("citations", []):
                cit_label = cit.get("citation") if isinstance(cit, dict) else str(cit)
                cit_id = _add_node(G, "Citation", cit_label)
                _add_edge(G, case_id, cit_id, "cites")

            # Granular Act -> Section mapping
            text = chunk.get("text", "")
            for sec_match in section_act_re.findall(text):
                section_num, act_name = sec_match
                act_name = act_name.strip()
                
                act_id = _add_node(G, "Act", act_name)
                sec_id = _add_node(G, "Section", f"Section {section_num} of {act_name}")
                
                _add_edge(G, act_id, sec_id, "contains")
                # Both directions
                _add_edge(G, sec_id, case_id, "interpreted_by")
                _add_edge(G, case_id, sec_id, "interprets")

        logger.info(f"[KG] Graph built: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
        return cls(G, chunks)

    def merge_chunks(self, new_chunks: List[Dict[str, Any]]) -> None:
        """
        Incrementally merge new document chunks into existing graph and chunk pool.
        """
        if not new_chunks:
            return

        section_act_re = re.compile(r"Section\s+([\w\.]+)\s+of\s+(?:the\s+)?([A-Za-z\s]+Act)", re.IGNORECASE)
        seen_ids = {c.get("chunk_id") for c in self._chunks if c.get("chunk_id")}

        for chunk in new_chunks:
            case_label = chunk.get("case_title", "Unknown")
            court_label = chunk.get("court", "Unknown Court")
            date = chunk.get("date", "")

            case_id = _add_node(self._G, "Case", case_label, date=date)
            court_id = _add_node(self._G, "Court", court_label)
            _add_edge(self._G, case_id, court_id, "decided_by")

            resolved = chunk.get("resolved_citations", {})
            for original, resolved_case in resolved.items():
                if resolved_case:
                    cit_case_id = _add_node(self._G, "Case", resolved_case)
                    _add_edge(self._G, case_id, cit_case_id, "cites_resolved", citation=original)

            for cit in chunk.get("citations", []):
                cit_label = cit.get("citation") if isinstance(cit, dict) else str(cit)
                cit_id = _add_node(self._G, "Citation", cit_label)
                _add_edge(self._G, case_id, cit_id, "cites")

            text = chunk.get("text", "")
            for section_num, act_name in section_act_re.findall(text):
                act_name = act_name.strip()
                act_id = _add_node(self._G, "Act", act_name)
                sec_id = _add_node(self._G, "Section", f"Section {section_num} of {act_name}")
                _add_edge(self._G, act_id, sec_id, "contains")
                _add_edge(self._G, sec_id, case_id, "interpreted_by")
                _add_edge(self._G, case_id, sec_id, "interprets")

            cid = chunk.get("chunk_id")
            if cid and cid not in seen_ids:
                self._chunks.append(chunk)
                seen_ids.add(cid)

    def save(self, path: Path = config.GRAPH_PATH) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            pickle.dump({"graph": self._G, "chunks": self._chunks}, fh)

    @classmethod
    def load(cls, path: Path = config.GRAPH_PATH) -> "KnowledgeGraph":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Knowledge graph not found: {path}")
        with open(path, "rb") as fh:
            data = pickle.load(fh)
        return cls(data["graph"], data["chunks"])

    def get_related_chunks(self, query: str, top_k: int = config.GRAPH_TOP_K) -> List[Dict[str, Any]]:
        query_lower = query.lower()
        matched_cases = set()

        for nid, attrs in self._G.nodes(data=True):
            if attrs.get("type") in ["Case", "Section", "Act"]:
                if any(tok in attrs.get("label", "").lower() for tok in query_lower.split() if len(tok) > 3):
                    matched_cases.add(nid)

        if not matched_cases:
            return []

        related_case_labels = set()
        for nid in matched_cases:
            if self._G.nodes[nid].get("type") == "Case":
                related_case_labels.add(self._G.nodes[nid].get("label", ""))
                
            for _, neighbor_nid in self._G.out_edges(nid):
                if self._G.nodes[neighbor_nid].get("type") == "Case":
                    related_case_labels.add(self._G.nodes[neighbor_nid].get("label", ""))

        results = []
        for chunk in self._chunks:
            if chunk.get("case_title", "") in related_case_labels:
                results.append(chunk)
            if len(results) >= top_k:
                break
                
        return results

    def get_cases_by_statute(self, statute_fragment: str, top_k: int = 10) -> List[Dict]:
        """Query specific cases that interpret a statute section"""
        query_lower = statute_fragment.lower()
        matched_sections = []
        for nid, attrs in self._G.nodes(data=True):
            if attrs.get("type") == "Section":
                if query_lower in attrs.get("label", "").lower():
                    matched_sections.append(nid)
                    
        cases = []
        for sec_nid in matched_sections:
            for _, dst in self._G.out_edges(sec_nid):
                if self._G.nodes[dst].get("type") == "Case":
                    cases.append(self._G.nodes[dst].get("label"))
                    
        # Return chunks from these cases
        results = []
        for chunk in self._chunks:
            if chunk.get("case_title", "") in cases:
                results.append(chunk)
            if len(results) >= top_k:
                break
        return results

    def get_citation_chain(self, case_title: str, depth: int = 2) -> Dict:
        """Returns nodes and edges for React Flow visualization"""
        edges = []
        nodes = []
        
        nid = _node_id("Case", case_title)
        if not self._G.has_node(nid):
            # Fuzzy fallback: pick closest case node by token overlap.
            q_tokens = {t for t in re.findall(r"[a-z0-9]+", case_title.lower()) if len(t) > 2}
            best_node = None
            best_score = 0
            for node_id, attrs in self._G.nodes(data=True):
                if attrs.get("type") != "Case":
                    continue
                label = attrs.get("label", "")
                l_tokens = {t for t in re.findall(r"[a-z0-9]+", label.lower()) if len(t) > 2}
                if not q_tokens or not l_tokens:
                    continue
                score = len(q_tokens.intersection(l_tokens))
                if score > best_score:
                    best_score = score
                    best_node = node_id
            if best_node:
                nid = best_node
            else:
                return {"nodes": [], "edges": []}
            
        tree = nx.bfs_edges(self._G, source=nid, depth_limit=depth)
        
        visited = set([nid])
        nodes.append({"id": nid, "data": {"label": self._G.nodes[nid].get("label")}})
        
        for u, v in tree:
            if v not in visited:
                nodes.append({"id": v, "data": {"label": self._G.nodes[v].get("label")}})
                visited.add(v)
            
            rel = self._G.edges[u,v].get("relation", "cites")
            edges.append({"id": f"{u}-{v}", "source": u, "target": v, "label": rel})
            
        return {"nodes": nodes, "edges": edges}

    @property
    def graph(self) -> nx.DiGraph:
        return self._G
