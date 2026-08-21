"""
Module 6 — Knowledge Graph Module
Builds a directed legal knowledge graph using NetworkX.

Node types  : Case | Act | Court | Citation
Edge types  : cites | refers_to | decided_by
Serialised  : outputs/knowledge_graph.gpickle
"""

import logging
import pickle
from pathlib import Path
from typing import List, Dict, Any, Set

import networkx as nx

import config

logger = logging.getLogger(__name__)


# ── Node / Edge helpers ────────────────────────────────────────────────────────

def _node_id(node_type: str, label: str) -> str:
    """Create a stable node ID from type + label."""
    return f"{node_type}::{label.strip()}"


def _add_node(G: nx.DiGraph, node_type: str, label: str, **attrs) -> str:
    nid = _node_id(node_type, label)
    if not G.has_node(nid):
        G.add_node(nid, type=node_type, label=label, **attrs)
    return nid


def _add_edge(G: nx.DiGraph, src: str, dst: str, relation: str) -> None:
    if not G.has_edge(src, dst):
        G.add_edge(src, dst, relation=relation)


# ── Builder ────────────────────────────────────────────────────────────────────

class KnowledgeGraph:
    """
    Legal Knowledge Graph over extracted entities and citations.

    Schema
    ------
    Nodes: Case, Act, Court, Citation
    Edges:
        Case  → cites      → Case
        Case  → refers_to  → Act
        Case  → decided_by → Court
    """

    def __init__(self, graph: nx.DiGraph, chunks: List[Dict[str, Any]]):
        self._G      = graph
        self._chunks = chunks  # kept for chunk lookup by doc_id

    # ── Build ──────────────────────────────────────────────────────────────────

    @classmethod
    def build(
        cls,
        documents: List[Dict[str, Any]],
        chunks: List[Dict[str, Any]],
    ) -> "KnowledgeGraph":
        G = nx.DiGraph()

        for doc in documents:
            meta     = doc.get("metadata", {}) or {}
            entities = doc.get("entities", []) or []
            citations = doc.get("citations", []) or []

            # ── Case node ─────────────────────────────────────────────────────
            if isinstance(meta, dict):
                case_label = (
                    meta.get("case_title")
                    or meta.get("title")
                    or meta.get("case_name")
                    or doc.get("_source_file", "Unknown")
                )
                court_label = meta.get("court") or meta.get("court_name") or "Unknown Court"
                date        = meta.get("date") or ""
            else:
                case_label  = str(meta)
                court_label = "Unknown Court"
                date        = ""

            case_id  = _add_node(G, "Case",  case_label, date=date,
                                  source=doc.get("_source_file", ""))
            court_id = _add_node(G, "Court", court_label)
            _add_edge(G, case_id, court_id, "decided_by")

            # ── Citation nodes ────────────────────────────────────────────────
            for cit in citations:
                if isinstance(cit, dict):
                    cit_label = (
                        cit.get("citation")
                        or cit.get("text")
                        or cit.get("id")
                        or str(cit)
                    )
                    # If the citation looks like a case reference vs an act
                    cit_type = "Citation"
                else:
                    cit_label = str(cit)
                    cit_type  = "Citation"

                cit_id = _add_node(G, cit_type, cit_label)
                _add_edge(G, case_id, cit_id, "cites")

            # ── Entity nodes (Acts, etc.) ─────────────────────────────────────
            for ent in entities:
                if isinstance(ent, dict):
                    ent_type  = ent.get("type", "").upper()
                    ent_label = ent.get("text") or ent.get("value") or str(ent)
                else:
                    ent_type  = "ENTITY"
                    ent_label = str(ent)

                if ent_type in {"ACT", "STATUTE", "LAW", "LEGISLATION"}:
                    act_id = _add_node(G, "Act", ent_label)
                    _add_edge(G, case_id, act_id, "refers_to")

        logger.info(
            f"[KG] Graph built: {G.number_of_nodes()} nodes, "
            f"{G.number_of_edges()} edges"
        )
        return cls(G, chunks)

    # ── Persist ────────────────────────────────────────────────────────────────

    def save(self, path: Path = config.GRAPH_PATH) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            pickle.dump({"graph": self._G, "chunks": self._chunks}, fh)
        logger.info(f"[KG] Graph saved → {path}")

    @classmethod
    def load(cls, path: Path = config.GRAPH_PATH) -> "KnowledgeGraph":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Knowledge graph not found: {path}")
        with open(path, "rb") as fh:
            data = pickle.load(fh)
        G      = data["graph"]
        chunks = data["chunks"]
        logger.info(
            f"[KG] Loaded graph: {G.number_of_nodes()} nodes, "
            f"{G.number_of_edges()} edges, {len(chunks)} chunks"
        )
        return cls(G, chunks)

    # ── Query ──────────────────────────────────────────────────────────────────

    def get_related_chunks(
        self,
        query: str,
        top_k: int = config.GRAPH_TOP_K,
    ) -> List[Dict[str, Any]]:
        """
        Find chunks from cases related (1-hop BFS) to any case whose label
        partially matches the query string.

        Returns list of chunk dicts (may be empty if no match).
        """
        query_lower = query.lower()
        matched_cases: Set[str] = set()

        # Seed: cases whose label contains query terms
        for nid, attrs in self._G.nodes(data=True):
            if attrs.get("type") == "Case":
                if any(tok in attrs.get("label", "").lower() for tok in query_lower.split()):
                    matched_cases.add(nid)

        if not matched_cases:
            return []

        # 1-hop expansion: follow outgoing edges
        related_case_labels: Set[str] = set()
        for case_nid in matched_cases:
            related_case_labels.add(
                self._G.nodes[case_nid].get("label", "")
            )
            for _, neighbor_nid in self._G.out_edges(case_nid):
                if self._G.nodes[neighbor_nid].get("type") == "Case":
                    related_case_labels.add(
                        self._G.nodes[neighbor_nid].get("label", "")
                    )

        # Retrieve chunks from those cases
        results: List[Dict[str, Any]] = []
        for chunk in self._chunks:
            if chunk.get("case_title", "") in related_case_labels:
                results.append(chunk)
            if len(results) >= top_k:
                break

        logger.debug(f"[KG] Graph expansion returned {len(results)} chunks.")
        return results

    @property
    def graph(self) -> nx.DiGraph:
        return self._G

    def stats(self) -> Dict[str, int]:
        type_counts: Dict[str, int] = {}
        for _, attrs in self._G.nodes(data=True):
            t = attrs.get("type", "Unknown")
            type_counts[t] = type_counts.get(t, 0) + 1
        edge_counts: Dict[str, int] = {}
        for _, _, attrs in self._G.edges(data=True):
            r = attrs.get("relation", "unknown")
            edge_counts[r] = edge_counts.get(r, 0) + 1
        return {"nodes": type_counts, "edges": edge_counts}
