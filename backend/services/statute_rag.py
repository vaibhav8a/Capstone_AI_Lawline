"""
statute_rag.py — the production statute question-answering pipeline.

    User Query
        ↓  corpus selection (IPC / BNS / both)
    Query Processing
        ↓
    BGE-M3 Embedding                (1024-d, no instruction prefix)
        ↓
    ChromaDB Dense Retrieval        (cosine, metadata-filtered by `law`)
        ↓
    Top-K Legal Evidence
        ↓
    RETRIEVAL GATE                  ── fails closed: no evidence, no API call ──
        ↓                                            ↓
    Context Construction                        ABSTAIN
        ↓
    Groq LLM                        (closed-book prompt; context is the only source)
        ↓
    Post-generation verification    (unsupported specifics → retry, then mark)
        ↓
    Grounded Answer + Citations

The model is treated as a generator and formatter of retrieved text, never as a
source of legal knowledge. Three independent layers enforce that — the gate
(`retrieval_gate.py`), the closed-book prompt and the verifier (`grounding.py`) —
because a system prompt alone is not a safety mechanism.

Configuration comes from config.py, which records why each value was chosen.
Hybrid retrieval and cross-encoder reranking are deliberately NOT in this path:
they measured slower and lower-recall than dense-only under bge-m3. They remain
implemented and reproducible through `evaluation/evaluate_retrieval.py`.
"""

from __future__ import annotations

import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, AsyncGenerator

sys.path.append(str(Path(__file__).resolve().parents[2]))

import config  # noqa: E402
from backend.services.abstention import (  # noqa: E402
    AbstentionDecision,
    AbstentionSignals,
    assess,
)
from backend.services.grounding import (  # noqa: E402
    CLOSED_BOOK_SYSTEM_PROMPT,
    INSUFFICIENT_EVIDENCE,
    STRICTER_RETRY_PROMPT,
    GroundingReport,
    annotate_unsupported,
    verify_answer,
)
from backend.services.retrieval_gate import evaluate_gate  # noqa: E402
from backend.services.corpus_selector import (  # noqa: E402
    chroma_filter,
    disclosure,
    select_corpus,
)

logger = logging.getLogger(__name__)

DISCLAIMER = (
    "This is legal information generated from public statutory text, not legal "
    "advice. It is not a substitute for a qualified advocate. Verify any "
    "provision against the official source before relying on it."
)

# The generation contract now lives in backend/services/grounding.py as
# CLOSED_BOOK_SYSTEM_PROMPT. The previous prompt here allowed the model to act as
# an "expert legal assistant", which licensed it to answer from pretrained
# knowledge whenever retrieval was thin — the exact behaviour this system must
# prevent. It has been removed rather than left as a tempting fallback.


class StatuteRAG:
    """Dense retrieval over the production statute collection, plus generation."""

    def __init__(self) -> None:
        self._collection = None
        self._judgments = None
        self._model = None
        self._groq = None

    # ── lazy resources ──────────────────────────────────────────────────────
    def _get_collection(self):
        if self._collection is None:
            import chromadb
            from chromadb.config import Settings

            client = chromadb.PersistentClient(
                path=str(config.CHROMA_PERSIST_PATH),
                settings=Settings(anonymized_telemetry=False),
            )
            names = {c.name for c in client.list_collections()}
            if config.STATUTE_COLLECTION not in names:
                raise RuntimeError(
                    f"production collection {config.STATUTE_COLLECTION!r} not found. "
                    "Run: python -m backend.ingestion.build_production_index"
                )
            self._collection = client.get_collection(config.STATUTE_COLLECTION)
        return self._collection

    def _get_model(self):
        if self._model is None:
            from backend.ingestion.build_index import get_model

            self._model = get_model(config.STATUTE_EMBED_MODEL_KEY)
        return self._model

    def _get_groq(self):
        if self._groq is None:
            if not config.GROQ_API_KEY:
                return None
            from groq import AsyncGroq

            self._groq = AsyncGroq(api_key=config.GROQ_API_KEY)
        return self._groq

    @property
    def llm_available(self) -> bool:
        return bool(config.GROQ_API_KEY)

    # ── retrieval ───────────────────────────────────────────────────────────
    def retrieve(
        self, query: str, corpus_override: str | None = None, top_k: int | None = None
    ) -> dict[str, Any]:
        top_k = top_k or config.STATUTE_TOP_K
        choice = select_corpus(query, corpus_override)
        collection = self._get_collection()

        start = time.perf_counter()
        vector = self._get_model().encode(
            config.BGE_QUERY_PREFIX + query,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        embed_ms = (time.perf_counter() - start) * 1000

        search_start = time.perf_counter()
        where = chroma_filter(choice)
        raw = collection.query(
            query_embeddings=[vector.tolist()],
            n_results=min(config.STATUTE_CANDIDATE_K, collection.count()),
            include=["metadatas", "distances", "documents"],
            **({"where": where} if where else {}),
        )
        search_ms = (time.perf_counter() - search_start) * 1000

        results = []
        for meta, distance in zip(raw["metadatas"][0], raw["distances"][0]):
            results.append(
                {
                    "law": meta.get("law", meta.get("document", "?")),
                    "section": meta.get("section", "?"),
                    "title": meta.get("title", ""),
                    "chapter": meta.get("chapter", ""),
                    "chapter_title": meta.get("chapter_title", ""),
                    "text": meta.get("section_text", ""),
                    "source": meta.get("source", "India Code"),
                    "url": meta.get("url", ""),
                    "act_title": meta.get("act_title", ""),
                    "legal_status": meta.get("legal_status", ""),
                    "amended_up_to": meta.get("amended_up_to", ""),
                    "superseded_note": meta.get("superseded_note", ""),
                    # Chroma returns cosine distance; convert to similarity.
                    "retrieval_score": round(1.0 - float(distance), 4),
                }
            )

        results = _promote_exact_section(results, choice)
        decision = assess(results, choice)
        context = results[:top_k]

        return {
            "query": query,
            "corpus": choice.to_dict(),
            "corpus_disclosure": disclosure(choice),
            "sources": context,
            "all_candidates": results,
            "abstention": decision.to_dict(),
            "timings_ms": {
                "embed": round(embed_ms, 2),
                "search": round(search_ms, 2),
                "total_retrieval": round(embed_ms + search_ms, 2),
            },
            "config": {
                "collection": config.STATUTE_COLLECTION,
                "embedding_model": config.EMBEDDING_MODEL,
                "embedding_dim": config.EMBEDDING_DIM,
                "chunk_strategy": config.STATUTE_CHUNK_STRATEGY,
                "retrieval": "dense",
            },
        }

    # ── judgment retrieval ──────────────────────────────────────────────────
    def _get_judgment_collection(self):
        if self._judgments is None:
            import chromadb
            from chromadb.config import Settings

            client = chromadb.PersistentClient(
                path=str(config.CHROMA_PERSIST_PATH),
                settings=Settings(anonymized_telemetry=False),
            )
            if config.JUDGMENT_COLLECTION not in {c.name for c in client.list_collections()}:
                raise RuntimeError(
                    f"judgment collection {config.JUDGMENT_COLLECTION!r} not found. "
                    "Run: python -m backend.ingestion.index_judgments"
                )
            self._judgments = client.get_collection(config.JUDGMENT_COLLECTION)
        return self._judgments

    @property
    def judgments_available(self) -> bool:
        try:
            return self._get_judgment_collection().count() > 0
        except Exception:
            return False

    def retrieve_judgments(
        self, query: str, top_k: int | None = None, section: str | None = None
    ) -> list[dict]:
        """Dense retrieval over the judgment corpus.

        `section` narrows to judgments that cite a given provision, which is what
        makes "what has the Supreme Court said about IPC 302?" answerable: the
        section is matched against `sections_referred` metadata rather than left
        to semantic similarity alone.
        """
        top_k = top_k or config.JUDGMENT_TOP_K
        collection = self._get_judgment_collection()

        vector = self._get_model().encode(
            config.BGE_QUERY_PREFIX + query,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        raw = collection.query(
            query_embeddings=[vector.tolist()],
            n_results=min(config.JUDGMENT_CANDIDATE_K, collection.count()),
            include=["metadatas", "distances"],
        )

        results = []
        for meta, distance in zip(raw["metadatas"][0], raw["distances"][0]):
            results.append(
                {
                    "source_type": "judgment",
                    "document_type": "judgment",
                    "law": meta.get("law", ""),
                    "case_name": meta.get("case_name", ""),
                    "court": meta.get("court", ""),
                    "judgment_date": meta.get("judgment_date", ""),
                    "citation": meta.get("citation", ""),
                    "neutral_citation": meta.get("neutral_citation", ""),
                    "judge": meta.get("judge", ""),
                    "sections_referred": [
                        s.strip() for s in str(meta.get("sections_referred", "")).split(",") if s.strip()
                    ],
                    "text": meta.get("passage_text", ""),
                    "source": meta.get("source", ""),
                    "url": meta.get("url", ""),
                    "document_id": meta.get("chunk_id", ""),
                    "retrieval_score": round(1.0 - float(distance), 4),
                }
            )

        if section:
            wanted = section.upper()
            citing = [r for r in results if wanted in [s.upper() for s in r["sections_referred"]]]
            others = [r for r in results if r not in citing]
            results = citing + others

        return results[:top_k]

    # ── context construction ────────────────────────────────────────────────
    @staticmethod
    def build_context(sources: list[dict]) -> str:
        """Render retrieved evidence, and nothing else, for the model.

        Carries no application state, no configuration, no chat history and no
        legal knowledge of its own — only the retrieved passages and the metadata
        needed to cite them.
        """
        blocks = []
        for index, source in enumerate(sources, start=1):
            if source.get("source_type") == "judgment":
                header = (
                    f"[{index}] TYPE: judgment | COURT: {source.get('court', '')} | "
                    f"CASE: {source.get('case_name', '')}"
                )
                lines = [
                    header,
                    f"DATE: {source.get('judgment_date', '')} | CITATION: {source.get('citation', '')}",
                ]
                if source.get("sections_referred"):
                    lines.append(
                        "SECTIONS REFERRED: "
                        + ", ".join(str(s) for s in source["sections_referred"][:15])
                    )
            else:
                header = (
                    f"[{index}] TYPE: statute | LAW: {source['law']} | "
                    f"SECTION: {source['section']} | TITLE: {source['title']}"
                )
                status = f"STATUS: {source.get('legal_status', 'unknown')}"
                if source.get("legal_status") == "repealed":
                    status += " (repealed w.e.f. 2024-07-01, replaced by BNS)"
                lines = [header, status]
                if source.get("superseded_note"):
                    lines.append(f"SUPERSEDED: {source['superseded_note']}")
            lines.append(f"TEXT: {source.get('text', '')}")
            blocks.append("\n".join(lines))
        return "\n\n---\n\n".join(blocks)

    # ── generation ──────────────────────────────────────────────────────────
    async def answer(
        self,
        query: str,
        corpus_override: str | None = None,
        top_k: int | None = None,
        *,
        verify: bool = True,
    ) -> dict[str, Any]:
        """Retrieve → gate → generate (closed-book) → verify → answer.

        The gate runs before any API call: when retrieval fails to produce usable
        evidence the model is never invoked, so it cannot answer from pretrained
        knowledge. See `retrieval_gate.py`.
        """
        retrieval = self.retrieve(query, corpus_override, top_k)

        gate = evaluate_gate(retrieval["sources"], _decision_from(retrieval["abstention"]))
        retrieval["gate"] = gate.to_dict()

        if not gate.allow_generation:
            return {
                **retrieval,
                "answer": INSUFFICIENT_EVIDENCE,
                "grounded": False,
                "sources": [],
                "abstained": True,
                "llm_used": False,
                "disclaimer": DISCLAIMER,
            }

        client = self._get_groq()
        if client is None:
            # No key: reproduce the retrieved provisions verbatim. This path can
            # produce no ungrounded claim because it generates nothing.
            return {
                **retrieval,
                "answer": self._extractive_fallback(retrieval["sources"]),
                "grounded": True,
                "abstained": False,
                "llm_used": False,
                "disclaimer": DISCLAIMER,
                "note": "GROQ_API_KEY is not configured; retrieved provisions are shown verbatim without generation.",
            }

        context = self.build_context(retrieval["sources"])
        start = time.perf_counter()

        text, attempts = await self._generate_verified(client, query, context, verify)

        generation_ms = (time.perf_counter() - start) * 1000
        retrieval["timings_ms"]["generation"] = round(generation_ms, 2)
        retrieval["timings_ms"]["total"] = round(
            retrieval["timings_ms"]["total_retrieval"] + generation_ms, 2
        )

        report = verify_answer(text, context) if verify else GroundingReport(True, note="verification disabled")
        refused = INSUFFICIENT_EVIDENCE.lower() in text.lower()

        return {
            **retrieval,
            "answer": text,
            "grounded": report.grounded,
            "grounding": report.to_dict(),
            "generation_attempts": attempts,
            "abstained": refused,
            "llm_used": True,
            "model": config.GROQ_MODEL,
            "cited_sections": extract_citations(text),
            "disclaimer": DISCLAIMER,
        }

    async def _generate_verified(
        self, client, query: str, context: str, verify: bool
    ) -> tuple[str, int]:
        """Generate, verify, and retry once under a stricter prompt if needed.

        Option B from the grounding design: an answer carrying unsupported legal
        specifics is regenerated rather than patched. If the retry is still
        unsupported, the unsupported parts are marked explicitly (Option C) rather
        than silently returned as fact.
        """
        text = await self._call_llm(client, CLOSED_BOOK_SYSTEM_PROMPT, query, context)
        if not verify:
            return text, 1

        report = verify_answer(text, context)
        if report.grounded:
            return text, 1

        logger.warning(
            "[grounding] attempt 1 produced %d unsupported claim(s): %s",
            len(report.unsupported),
            [c.value for c in report.unsupported][:5],
        )
        retry = await self._call_llm(client, STRICTER_RETRY_PROMPT, query, context)
        retry_report = verify_answer(retry, context)
        if retry_report.grounded:
            return retry, 2

        logger.warning(
            "[grounding] attempt 2 still unsupported (%d); marking claims",
            len(retry_report.unsupported),
        )
        return annotate_unsupported(retry, retry_report), 2

    @staticmethod
    async def _call_llm(client, system_prompt: str, query: str, context: str) -> str:
        """One closed-book call. The payload is the prompt, the context, the question.

        Nothing else is sent: no chat history, no application state, no configuration.
        """
        response = await client.chat.completions.create(
            model=config.GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"LEGAL CONTEXT:\n{context}\n\n"
                        f"USER QUESTION:\n{query}\n\n"
                        "Answer the user's question using ONLY the supplied LEGAL CONTEXT."
                    ),
                },
            ],
            temperature=0.0,
        )
        return response.choices[0].message.content or ""

    @staticmethod
    def _extractive_fallback(sources: list[dict]) -> str:
        """No LLM configured: show the statutory text rather than nothing.

        This path never paraphrases — it reproduces the retrieved provisions
        verbatim, so there is no possibility of an ungrounded claim.
        """
        if not sources:
            return INSUFFICIENT_EVIDENCE
        lines = [
            "_No language model is configured, so the retrieved provisions are "
            "shown verbatim without explanation._\n",
        ]
        for source in sources:
            lines.append(f"### {source['law']} Section {source['section']} — {source['title']}")
            if source.get("legal_status") == "repealed":
                lines.append(
                    "> **Repealed** with effect from 1 July 2024; replaced by the "
                    "Bharatiya Nyaya Sanhita, 2023."
                )
            if source.get("superseded_note"):
                lines.append(f"> **Superseded text:** {source['superseded_note']}")
            lines.append(f"\n{source.get('text', '')}\n")
        return "\n".join(lines)


def _promote_exact_section(results: list[dict], choice) -> list[dict]:
    """Move an explicitly requested section to rank 1.

    Dense retrieval has a specific, reproducible failure on section-number
    queries: "What does IPC Section 420 deal with?" returns s.40 ("Offence") and
    s.50 ("Section") above s.420, because the literal words "Section" and
    "Offence" are themselves section titles. Semantic similarity has no notion
    that "420" is an identifier rather than a topic.

    When the user names a section and that exact section is present in the
    candidates, promoting it is not a heuristic guess — it is an exact key match
    on structured metadata, and it is unambiguously the right answer.

    This is a production-only step. It is deliberately absent from the
    experimental configurations in evaluation/, so the saved retrieval metrics
    continue to describe the configurations they were measured on. The effect is
    documented in docs/architecture.md rather than folded into those numbers.
    """
    if not choice.section:
        return results

    wanted = choice.section.upper()
    exact = [
        r for r in results
        if str(r.get("section", "")).upper() == wanted
        and (choice.law is None or r.get("law") == choice.law)
    ]
    if not exact:
        return results

    rest = [r for r in results if r not in exact]
    for row in exact:
        row["exact_section_match"] = True
    return exact + rest


def _decision_from(payload: dict) -> AbstentionDecision:
    """Rebuild an AbstentionDecision from its serialised form for the gate."""
    signals = AbstentionSignals(**payload.get("signals", {}))
    return AbstentionDecision(
        should_abstain=payload.get("should_abstain", False),
        confidence=payload.get("confidence", "none"),
        reasons=payload.get("reasons", []),
        signals=signals,
    )


CITATION_RE = re.compile(r"\[(IPC|BNS)\s+s\.?\s*(\d{1,3}[A-Za-z]{0,2})\]", re.I)


def extract_citations(text: str) -> list[dict]:
    """Pull the [IPC s.420] style citations the model was told to emit."""
    seen: set[tuple[str, str]] = set()
    out = []
    for law, section in CITATION_RE.findall(text):
        key = (law.upper(), section.upper())
        if key not in seen:
            seen.add(key)
            out.append({"law": key[0], "section": key[1]})
    return out


statute_rag = StatuteRAG()
