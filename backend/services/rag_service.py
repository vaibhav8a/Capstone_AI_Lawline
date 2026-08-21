"""
rag_service.py
The master orchestrator for the backend.
Instantiates all RAG modules on startup and exposes simple API methods for routers.
"""

import logging
from typing import List, Dict, Any, AsyncGenerator

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))

from backend.modules.chroma_store import ChromaStore
from backend.modules.bm25_index import BM25Index
from backend.modules.knowledge_graph import KnowledgeGraph
from backend.modules.hybrid_retriever import HybridRetriever
from backend.modules.reranker import rerank_two_pass
from backend.modules.query_engine import QueryEngine
from backend.modules.parent_retriever import ParentRetriever
from backend.modules.case_summarizer import CaseSummarizer
from backend.modules.precedent_checker import PrecedentChecker
from backend.services.cache_service import CacheService

import config

logger = logging.getLogger(__name__)

class RAGService:
    def __init__(self):
        self.chroma = None
        self.bm25 = None
        self.kg = None
        self.hybrid_retriever = None
        self.query_engine = None
        self.parent_retriever = None
        self.case_summarizer = None
        self.precedent_checker = None
        self.cache = CacheService()
        self.initialized = False

    async def initialize(self):
        """Loads all stores and models into memory."""
        logger.info("[RAGService] Starting initialization sequence...")
        
        self.chroma = ChromaStore()
        
        try:
            self.bm25 = BM25Index.load()
        except FileNotFoundError:
            logger.warning("[RAGService] BM25 index not found. Skipping BM25.")
            
        try:
            self.kg = KnowledgeGraph.load()
        except FileNotFoundError:
            logger.warning("[RAGService] Knowledge Graph not found. Skipping KG.")
            
        # We need the full chunk payload for BM25 and KG hydrations.
        # Fallback to loading them from faiss meta if they exist, else we rely on Chroma.
        # If Chroma is primary, Hybrid Retriever needs adjusting to not rely on an all_chunks list
        # For this implementation, we assume we load chunks from BM25 index if available.
        all_chunks = self.bm25._chunks if self.bm25 else []
        
        self.hybrid_retriever = HybridRetriever(self.chroma, self.bm25, self.kg, all_chunks)
        self.query_engine = QueryEngine()
        self.parent_retriever = ParentRetriever()
        self.case_summarizer = CaseSummarizer()
        self.precedent_checker = PrecedentChecker()
        
        self.initialized = True
        logger.info("[RAGService] Initialization complete.")

    async def query(self, user_query: str, filters: Dict[str, Any] | None = None, use_hyde: bool = True, use_self_rag: bool = True) -> Dict[str, Any]:
        """
        Executes the full pipeline in parallel for high speed.
        """
        if not self.initialized:
            raise RuntimeError("RAGService not initialized")
            
        # Check Cache
        filters = filters or {}
        cached_res = self.cache.get("query_rag", query=user_query, filters=filters, use_hyde=use_hyde)
        if cached_res:
            return cached_res

        import asyncio

        # 1. Parallel Decomposition & HyDE
        sub_queries, hyde_doc = await asyncio.gather(
            self.query_engine.decompose_query_llm(user_query),
            self.query_engine.generate_hyde_document(user_query) if use_hyde else asyncio.sleep(0, result=user_query),
        )
        
        # 2. Parallel Retrieval for all sub-queries
        all_retrieved = []
        retrieval_queries = list(sub_queries)
        if hyde_doc and hyde_doc != user_query:
            retrieval_queries.append(hyde_doc)

        retrieval_results = await asyncio.gather(
            *[
                asyncio.to_thread(self.hybrid_retriever.retrieve_with_stats, sq)
                for sq in retrieval_queries
            ]
        )

        retrieval_stats = []
        for retrieved, stats in retrieval_results:
            all_retrieved.extend(retrieved)
            retrieval_stats.append(stats)
            
        # 3. Deduplicate
        seen = set()
        deduped = []
        for c in all_retrieved:
            cid = c.get("chunk_id")
            if cid not in seen:
                seen.add(cid)
                deduped.append(c)
                
        # 3b. Apply frontend/backend filters.
        if filters:
            filtered = []
            for chunk in deduped:
                court = (chunk.get("court") or "").lower()
                date = str(chunk.get("date") or "")
                year_match = __import__("re").search(r"\b(19\d{2}|20\d{2})\b", date)
                year = int(year_match.group(1)) if year_match else None
                section_type = (chunk.get("section_type") or "").lower()
                title = (chunk.get("case_title") or "").lower()
                text = (chunk.get("text") or "").lower()
                include = True

                courts = [c.lower() for c in filters.get("courts", [])]
                if courts and not any(c in court for c in courts):
                    include = False
                year_from = filters.get("yearFrom")
                year_to = filters.get("yearTo")
                if include and year is not None and year_from and year < int(year_from):
                    include = False
                if include and year is not None and year_to and year > int(year_to):
                    include = False
                topics = [t.lower() for t in filters.get("topics", [])]
                if include and topics and not any(t in text or t in title for t in topics):
                    include = False
                statuses = [s.lower() for s in filters.get("precedentStatus", [])]
                if include and statuses and section_type and section_type not in statuses and not any(s in text for s in statuses):
                    include = False

                if include:
                    filtered.append(chunk)
            deduped = filtered

        # 4. Parent Expansion
        expanded = self.parent_retriever.expand_to_parents(deduped)
        
        # 5. Two-pass Reranking
        top_k = config.RERANKER_TOP_K
        reranked = rerank_two_pass(user_query, expanded, top_k)

        # 6. Shepardization status annotations for surfaced chunks.
        for chunk in reranked:
            try:
                chunk["precedent_status"] = self.precedent_checker.check_temporal_validity(
                    chunk.get("case_title", ""),
                    deduped,
                )
            except Exception:
                chunk["precedent_status"] = {"status": "unknown", "confidence": 0.0}
        
        result = {
            "query": user_query,
            "sub_queries": sub_queries,
            "context_chunks": reranked,
            "retrieval_stats": retrieval_stats,
            "self_rag_enabled": bool(use_self_rag),
        }
        
        self.cache.set("query_rag", result, query=user_query, filters=filters, use_hyde=use_hyde)
        return result

    async def stream_answer(
        self,
        query: str,
        context_chunks: List[Dict],
        use_self_rag: bool = True,
        chat_history: List[Dict[str, str]] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Runs the query engine LLM and self-rag critic, yielding SSE tokens."""
        async for token in self.query_engine.execute_streaming(
            query,
            context_chunks,
            use_self_rag=use_self_rag,
            chat_history=chat_history or [],
        ):
            yield token

    # Additional endpoints for Case Summarizer, Conflicts, etc.
    async def get_summary(self, document_text: str):
        return await self.case_summarizer.summarize_case_async(document_text)

    def check_conflicts(self, query: str, chunks: List[Dict]) -> List[Dict]:
        return self.precedent_checker.detect_jurisdictional_conflicts(chunks, query)

# Singleton instance
rag_service = RAGService()
