"""
legal_rag.py — unified statute + judgment retrieval.

    USER QUERY
        │
        ▼
    Query understanding          classify type, validate premises
        │
   ┌────┴────┐
   ▼         ▼
 Statute   Judgment              two collections, never merged
 retrieval retrieval
   │         │
   └────┬────┘
        ▼
  Candidate pool
        ▼
  Case relevance scoring         semantic + section + law + cross-reference
        ▼
  Evidence selection             top statutes, top 3-5 judgments
        ▼
  Retrieval gate                 fails closed

Generation is deliberately not part of this module: this is the retrieval layer,
evaluated on its own before any LLM is involved.

Judgments are capped at 5 and each carries a stated reason for its inclusion, so
a user never receives ten unrelated decisions that merely mention a number.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).resolve().parents[2]))

import config  # noqa: E402
from backend.services.grounding import (  # noqa: E402
    INSUFFICIENT_EVIDENCE,
    verify_answer,
)
from backend.services.legal_registry import judgments_citing, sections_by_judgment  # noqa: E402
from backend.services.retrieval_gate import evaluate_gate  # noqa: E402
from backend.services.query_router import Routing, classify  # noqa: E402
from backend.services.statute_rag import DISCLAIMER, _decision_from, statute_rag  # noqa: E402

logger = logging.getLogger(__name__)

INSUFFICIENT = (
    "The available legal sources do not contain sufficient evidence to answer "
    "this question confidently."
)

MAX_JUDGMENTS = 5
MIN_JUDGMENT_SCORE = 0.30


@dataclass(frozen=True)
class CaseWeights:
    """Weights for the combined case-relevance score.

    Selected by grid search on the DEV split only
    (`evaluation/evaluate_legal_retrieval.py --tune`, 24 trials, objective nDCG@5);
    results in evaluation/results/case_weight_search.json.

    What the search actually showed, measured on DEV:

        pure semantic (metadata off)   nDCG@5 0.5588
        + section match                        0.6219
        + cross-reference                      0.6532
        + both  (these defaults)               0.6643
        weights raised 8x                      0.6643   <- identical

    So the metadata signals matter in KIND, not in DEGREE: turning them on is
    worth ~0.11 nDCG (about 19% relative), while their magnitude is irrelevant
    once large enough to reorder the candidate pool. Eight of the 24 trials tied
    exactly. The grid search therefore did not find a finely-tuned optimum and
    should not be described as having done so — it established that both signals
    help and that the system is insensitive to their exact values.

    `law_match` was selected at 0.0: it contributed nothing measurable, because a
    judgment retrieved for an IPC query is almost always already an IPC judgment.
    It is retained at zero rather than deleted so the ablation stays reproducible.

    After `section_match` was corrected to read the same proximity-attributed
    citation map the gold labels use, a re-run of the search selected
    `cross_reference` at 0.0 as well — tied with 0.25. The two signals had become
    largely redundant: both now answer "does this judgment cite the section the
    question names". 0.25 is retained because the tie is exact and the signal
    still fires for judgments reached semantically rather than by section match.
    """
    semantic: float = 1.0
    section_match: float = 0.25
    law_match: float = 0.0
    cross_reference: float = 0.25

    def to_dict(self) -> dict:
        return {
            "semantic": self.semantic,
            "section_match": self.section_match,
            "law_match": self.law_match,
            "cross_reference": self.cross_reference,
        }


DEFAULT_WEIGHTS = CaseWeights()


def score_judgment(
    candidate: dict,
    routing: Routing,
    cross_referenced: set[str],
    weights: CaseWeights = DEFAULT_WEIGHTS,
) -> tuple[float, list[str]]:
    """Combined relevance for one judgment passage, with its reasons.

    Ranking judgments by embedding similarity alone puts a passage that merely
    mentions a number above one that actually construes the provision. The extra
    signals are all exact metadata matches, so they are cheap and explainable.
    """
    reasons: list[str] = []
    score = weights.semantic * float(candidate.get("retrieval_score", 0.0))

    named = {section for _statute, section in routing.named_sections}
    # Use the proximity-attributed citation map, NOT the judgment's raw
    # `sections_referred`. The raw field is bare-number extraction that cannot
    # tell IPC s.4 from CrPC s.4, so it marked judgments as citing a section they
    # never cite under that statute.
    case_key = candidate.get("neutral_citation") or candidate.get("case_name", "")
    cited = sections_by_judgment().get(case_key, set())

    if named and (named & cited):
        overlap = sorted(named & cited)
        score += weights.section_match
        reasons.append(
            f"cites section {', '.join(overlap)}, which the question names"
        )

    named_laws = {statute for statute, _ in routing.named_sections}
    if named_laws and candidate.get("law", "") in named_laws:
        score += weights.law_match
        # Only claim this as a reason when it actually moved the score. The
        # measured weight is 0.0, so listing it in the UI would present a factor
        # that contributed nothing as though it had — the reasons shown to a user
        # must be the reasons the case ranked where it did.
        if weights.law_match > 0:
            reasons.append(f"decided under the {candidate['law']}")

    key = candidate.get("neutral_citation") or candidate.get("case_name", "")
    if key and key in cross_referenced:
        score += weights.cross_reference
        reasons.append("cross-referenced to the retrieved provision")

    if not reasons:
        reasons.append("matched the question semantically")

    return score, reasons


class LegalRAG:
    """Retrieval across statutes and judgments, routed by query type."""

    def retrieve(
        self,
        query: str,
        corpus_override: str | None = None,
        weights: CaseWeights = DEFAULT_WEIGHTS,
    ) -> dict[str, Any]:
        start = time.perf_counter()
        routing = classify(query)

        # Type E: a false premise means there is nothing honest to retrieve.
        if not routing.should_retrieve:
            return {
                "query": query,
                "routing": routing.to_dict(),
                "statutes": [],
                "judgments": [],
                "answerable": False,
                "reason": INSUFFICIENT,
                "premise_problems": routing.premises.problems,
                "timings_ms": {"total": round((time.perf_counter() - start) * 1000, 2)},
            }

        statute_result = statute_rag.retrieve(query, corpus_override)
        statutes = statute_result["sources"]

        # Cases cross-referenced to whichever provisions came back. This is what
        # turns "here is s.302" into "and here are decisions construing it".
        cross_referenced: set[str] = set()
        for source in statutes[:3]:
            for case in judgments_citing(source.get("law", ""), source.get("section", "")):
                key = case.get("neutral_citation") or case.get("case_name", "")
                if key:
                    cross_referenced.add(key)
        for statute, section in routing.named_sections:
            for case in judgments_citing(statute, section):
                key = case.get("neutral_citation") or case.get("case_name", "")
                if key:
                    cross_referenced.add(key)

        judgments: list[dict] = []
        if statute_rag.judgments_available:
            section_hint = routing.named_sections[0][1] if routing.named_sections else None
            candidates = statute_rag.retrieve_judgments(
                query, top_k=config.JUDGMENT_CANDIDATE_K, section=section_hint
            )
            scored = []
            for candidate in candidates:
                score, reasons = score_judgment(candidate, routing, cross_referenced, weights)
                scored.append({**candidate, "case_score": round(score, 4), "why_relevant": reasons})
            scored.sort(key=lambda c: c["case_score"], reverse=True)

            # One passage per case: several passages from one judgment is not
            # several authorities, and it crowds out genuinely distinct cases.
            seen_cases: set[str] = set()
            for candidate in scored:
                key = candidate.get("neutral_citation") or candidate.get("case_name", "")
                if key in seen_cases:
                    continue
                if candidate["case_score"] < MIN_JUDGMENT_SCORE:
                    continue
                seen_cases.add(key)
                judgments.append(candidate)
                if len(judgments) >= MAX_JUDGMENTS:
                    break

        elapsed = (time.perf_counter() - start) * 1000
        return {
            "query": query,
            "routing": routing.to_dict(),
            "statutes": statutes,
            "judgments": judgments,
            "answerable": bool(statutes or judgments),
            "corpus_disclosure": statute_result["corpus_disclosure"],
            "abstention": statute_result["abstention"],
            "weights": weights.to_dict(),
            "timings_ms": {
                "statute_retrieval": statute_result["timings_ms"]["total_retrieval"],
                "total": round(elapsed, 2),
            },
        }


    # ── generation ──────────────────────────────────────────────────────────
    async def answer(
        self,
        query: str,
        corpus_override: str | None = None,
        weights: CaseWeights = DEFAULT_WEIGHTS,
        *,
        verify: bool = True,
    ) -> dict[str, Any]:
        """Retrieve statutes + judgments, gate, then generate closed-book.

        Identical safety architecture to the statute-only path: the model is
        never called unless retrieval produced usable evidence, receives only
        that evidence, and its output is verified against it. The only change is
        that the evidence now spans two source types.
        """
        retrieval = self.retrieve(query, corpus_override, weights)

        # Type E — a false premise. Refuse before the gate even runs; there is
        # nothing to retrieve for a section that does not exist.
        if not retrieval["answerable"] and retrieval.get("premise_problems"):
            return {
                **retrieval,
                "answer": INSUFFICIENT,
                "grounded": False,
                "llm_used": False,
                "abstained": True,
                "disclaimer": DISCLAIMER,
            }

        evidence = retrieval["statutes"] + retrieval["judgments"]
        gate = evaluate_gate(evidence, _decision_from(retrieval["abstention"]))
        retrieval["gate"] = gate.to_dict()

        if not gate.allow_generation:
            return {
                **retrieval,
                "answer": INSUFFICIENT,
                "grounded": False,
                "statutes": [],
                "judgments": [],
                "llm_used": False,
                "abstained": True,
                "disclaimer": DISCLAIMER,
            }

        client = statute_rag._get_groq()
        if client is None:
            return {
                **retrieval,
                "answer": statute_rag._extractive_fallback(retrieval["statutes"]),
                "grounded": True,
                "llm_used": False,
                "abstained": False,
                "disclaimer": DISCLAIMER,
                "note": (
                    "GROQ_API_KEY is not configured; retrieved provisions are shown "
                    "verbatim and judgments are listed without generated summary."
                ),
            }

        context = statute_rag.build_context(evidence)
        start = time.perf_counter()
        text, attempts = await statute_rag._generate_verified(client, query, context, verify)
        generation_ms = (time.perf_counter() - start) * 1000

        report = verify_answer(text, context) if verify else None
        retrieval["timings_ms"]["generation"] = round(generation_ms, 2)

        return {
            **retrieval,
            "answer": text,
            "grounded": report.grounded if report else None,
            "grounding": report.to_dict() if report else None,
            "generation_attempts": attempts,
            "llm_used": True,
            "model": config.GROQ_MODEL,
            "abstained": INSUFFICIENT.lower() in text.lower()
            or INSUFFICIENT_EVIDENCE.lower() in text.lower(),
            "disclaimer": DISCLAIMER,
        }


legal_rag = LegalRAG()
