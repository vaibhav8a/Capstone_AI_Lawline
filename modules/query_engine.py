"""
Module 9 — Query Engine
Orchestrates the full RAG pipeline: retrieve → rerank → generate.
Supports Groq (default), OpenAI, Gemini, and Ollama backends.
"""

import logging
import time
from typing import List, Dict, Any, Optional
import os

import config
from modules.hybrid_retriever import HybridRetriever
from modules.reranker          import rerank

logger = logging.getLogger(__name__)


# ── Prompt Template ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a senior legal research assistant with deep expertise in case law, statutes, and judicial reasoning.
Your task is to answer legal questions accurately and concisely based ONLY on the provided context.

Rules:
1. Base your answer strictly on the context provided.
2. Cite the case or section when referencing a legal point.
3. If the context is insufficient, say "The provided documents do not contain enough information to answer this question."
4. Do NOT fabricate case names, statutes, or holdings.
5. Use formal legal language."""

def _build_user_prompt(query: str, chunks: List[Dict[str, Any]]) -> str:
    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        case  = chunk.get("case_title", "Unknown")
        court = chunk.get("court", "Unknown")
        section = chunk.get("section", "")
        text    = chunk.get("text", "")
        context_parts.append(
            f"[{i}] Case: {case} | Court: {court} | Section: {section}\n{text}"
        )
    context_block = "\n\n---\n\n".join(context_parts)
    return f"""LEGAL CONTEXT:
{context_block}

QUESTION: {query}

ANSWER:"""


# ── LLM Backends ──────────────────────────────────────────────────────────────

def _call_groq(system: str, user: str) -> str:
    from groq import Groq
    groq_key = os.getenv("GROQ_API_KEY") or config.GROQ_API_KEY
    if not (groq_key or "").strip():
        raise EnvironmentError(
            "GROQ_API_KEY not set. Export it or set RAG_LLM_BACKEND=stub."
        )
    client   = Groq(api_key=groq_key)
    response = client.chat.completions.create(
        model=config.GROQ_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        temperature=0.1,
        max_tokens=1024,
    )
    return response.choices[0].message.content.strip()


def _call_openai(system: str, user: str) -> str:
    import openai
    openai.api_key = config.OPENAI_API_KEY
    response = openai.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        temperature=0.1,
        max_tokens=1024,
    )
    return response.choices[0].message.content.strip()


def _call_gemini(system: str, user: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=config.GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name=config.GEMINI_MODEL,
        system_instruction=system,
    )
    response = model.generate_content(user)
    return response.text.strip()


def _call_ollama(system: str, user: str) -> str:
    import urllib.request, json as _json
    payload = _json.dumps({
        "model": config.OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        f"{config.OLLAMA_BASE_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = _json.loads(resp.read())
    return data["message"]["content"].strip()


def _call_stub(_system: str, user: str) -> str:
    """No-API placeholder — returns a template answer for testing."""
    return (
        "[STUB MODE] No LLM API key configured. "
        "Set GROQ_API_KEY (or another backend) to get real answers.\n\n"
        f"Query received: {user[:200]}..."
    )


_BACKENDS = {
    "groq":   _call_groq,
    "openai": _call_openai,
    "gemini": _call_gemini,
    "ollama": _call_ollama,
    "stub":   _call_stub,
}


def _generate(system: str, user: str) -> str:
    backend = config.LLM_BACKEND.lower()
    groq_key = os.getenv("GROQ_API_KEY") or config.GROQ_API_KEY
    if backend == "groq" and not (groq_key or "").strip():
        logger.warning("[QueryEngine] GROQ_API_KEY missing; using stub backend.")
        return _call_stub(system, user)
    fn = _BACKENDS.get(backend)
    if fn is None:
        logger.warning(f"[QueryEngine] Unknown backend '{backend}', using stub.")
        fn = _call_stub
    try:
        return fn(system, user)
    except EnvironmentError as exc:
        logger.warning("[QueryEngine] LLM call failed (%s); falling back to stub.", exc)
        return _call_stub(system, user)


# ── Query Engine ──────────────────────────────────────────────────────────────

class QueryEngine:
    """
    Full RAG pipeline:
        query → embed → hybrid retrieve → rerank → LLM → answer
    """

    def __init__(self, retriever: HybridRetriever):
        self._retriever = retriever

    def query(
        self,
        question: str,
        top_k: int = config.RERANKER_TOP_K,
    ) -> Dict[str, Any]:
        """
        Execute the full pipeline for a single question.

        Returns
        -------
        dict with keys: answer, sources, latency_ms, retrieved_count
        """
        t0_total = time.perf_counter()

        retrieve_start = time.perf_counter()
        if hasattr(self._retriever, "retrieve_with_stats"):
            candidates, retrieval_stats = self._retriever.retrieve_with_stats(
                query=question, top_k=config.HYBRID_TOP_K
            )
        else:
            candidates = self._retriever.retrieve(
                question, top_k=config.HYBRID_TOP_K
            )
            retrieval_stats = {}

        # ── Rerank ────────────────────────────────────────────────────────────
        reranked = rerank(question, candidates, top_k=top_k)

        # ── Build prompt ──────────────────────────────────────────────────────
        context_chunks = reranked[: config.MAX_CONTEXT_CHUNKS]
        user_prompt = _build_user_prompt(question, context_chunks)

        retrieve_end = time.perf_counter()
        retrieval_time_ms = int((retrieve_end - retrieve_start) * 1000)

        # ── Generate ──────────────────────────────────────────────────────────
        gen_start = time.perf_counter()
        answer = _generate(SYSTEM_PROMPT, user_prompt)
        gen_end = time.perf_counter()
        generation_time_ms = int((gen_end - gen_start) * 1000)

        latency_ms = int((time.perf_counter() - t0_total) * 1000)

        sources = [
            {
                "case_title": c.get("case_title", ""),
                "court":      c.get("court", ""),
                "section":    c.get("section", ""),
                "score":      c.get("_reranker_score", 0.0),
            }
            for c in context_chunks
        ]

        return {
            "answer":           answer,
            "sources":          sources,
            "latency_ms":       latency_ms,
            "retrieved_count":  len(candidates),
            "reranked_count":   len(reranked),
            "retrieval_time_ms": retrieval_time_ms,
            "generation_time_ms": generation_time_ms,
            "retrieval_stats": retrieval_stats,
            "reranked_chunks":    reranked,
        }

    # ── Interactive REPL ──────────────────────────────────────────────────────

    def run_repl(self) -> None:
        """Launch an interactive question-answering session."""
        from colorama import Fore, Style, init as colorama_init
        colorama_init(autoreset=True)

        print(f"\n{Fore.CYAN}{'═'*60}")
        print(f"  Legal RAG System — Interactive Mode")
        print(f"  Backend: {config.LLM_BACKEND.upper()}")
        print(f"{'═'*60}{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}Type your legal question and press Enter.")
        print(f"Type 'exit' or 'quit' to end the session.{Style.RESET_ALL}\n")

        while True:
            try:
                question = input(f"{Fore.GREEN}Query ▶  {Style.RESET_ALL}").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye.")
                break

            if not question:
                continue
            if question.lower() in {"exit", "quit", "q"}:
                print("Goodbye.")
                break

            print(f"{Fore.CYAN}Retrieving…{Style.RESET_ALL}")
            result = self.query(question)

            print(f"\n{Fore.WHITE}{'─'*60}")
            print(f"{Fore.YELLOW}Answer:{Style.RESET_ALL}")
            print(result["answer"])

            if result["sources"]:
                print(f"\n{Fore.CYAN}Sources:{Style.RESET_ALL}")
                for i, src in enumerate(result["sources"], 1):
                    print(
                        f"  [{i}] {src['case_title']} | {src['court']} "
                        f"| Score: {src['score']:.3f}"
                    )

            print(
                f"\n{Fore.WHITE}Latency: {result['latency_ms']} ms  |  "
                f"Retrieved: {result['retrieved_count']}  |  "
                f"Reranked: {result['reranked_count']}{Style.RESET_ALL}\n"
            )
