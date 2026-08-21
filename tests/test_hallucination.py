"""The "own brain" test suite.

These tests target one failure: the model answering from pretrained knowledge
when the corpus contains no supporting evidence. An LLM trained on the open web
has certainly read about the IPC, so it *can* produce a fluent, often correct
answer with no retrieval behind it — and a correct-sounding answer with no
evidence is precisely what this system must not emit.

Layers tested here:

  * the retrieval gate, which refuses before the API call (no key needed)
  * the claim verifier, which catches fabricated specifics (no key needed)
  * end-to-end behaviour with a live model (needs GROQ_API_KEY)

The gate and verifier tests run unconditionally, because they are the layers that
hold when the prompt does not.
"""

from __future__ import annotations

import pytest

import config
from backend.services.abstention import AbstentionDecision, AbstentionSignals
from backend.services.grounding import (
    INSUFFICIENT_EVIDENCE,
    annotate_unsupported,
    verify_answer,
)
from backend.services.retrieval_gate import evaluate_gate
from backend.services.statute_rag import statute_rag


def _index_available() -> bool:
    try:
        return statute_rag._get_collection().count() > 0
    except Exception:
        return False


needs_index = pytest.mark.skipif(not _index_available(), reason="production index not built")
needs_llm = pytest.mark.skipif(not config.GROQ_API_KEY, reason="GROQ_API_KEY not configured")


def _ok_decision():
    return AbstentionDecision(False, "high", [], AbstentionSignals(peak_similarity=0.8, support_count=3))


def _abstain_decision(reason="weak"):
    return AbstentionDecision(True, "none", [reason], AbstentionSignals(peak_similarity=0.2))


# ── Layer 1: the gate refuses before the model is reachable ─────────────────

class TestRetrievalGate:
    def test_no_evidence_blocks_generation(self):
        decision = evaluate_gate([], _ok_decision())
        assert not decision.allow_generation

    def test_weak_evidence_blocks_generation(self):
        sources = [{"retrieval_score": 0.20, "law": "IPC", "section": "1"}]
        assert not evaluate_gate(sources, _ok_decision()).allow_generation

    def test_abstention_advice_blocks_generation(self):
        sources = [{"retrieval_score": 0.90, "law": "IPC", "section": "302"}]
        assert not evaluate_gate(sources, _abstain_decision()).allow_generation

    def test_strong_evidence_allows_generation(self):
        sources = [{"retrieval_score": 0.75, "law": "IPC", "section": "302"}]
        assert evaluate_gate(sources, _ok_decision()).allow_generation

    def test_gate_reports_its_reason(self):
        decision = evaluate_gate([], _ok_decision())
        assert decision.reason and decision.evidence_count == 0


# ── Layer 3: fabricated specifics are detected ─────────────────────────────

CONTEXT = (
    "[1] TYPE: statute | LAW: IPC | SECTION: 420 | TITLE: Cheating and dishonestly "
    "inducing delivery of property\nSTATUS: repealed\n"
    "TEXT: Whoever cheats and thereby dishonestly induces the person deceived to "
    "deliver any property shall be punished with imprisonment of either description "
    "for a term which may extend to seven years, and shall also be liable to fine."
)


class TestClaimVerification:
    def test_supported_answer_passes(self):
        answer = "Section 420 covers cheating and may attract imprisonment up to seven years [1]."
        assert verify_answer(answer, CONTEXT).grounded

    def test_fabricated_section_number_detected(self):
        answer = "This is dealt with under section 511 of the Code."
        report = verify_answer(answer, CONTEXT)
        assert not report.grounded
        assert any(c.kind == "section" for c in report.unsupported)

    def test_fabricated_case_citation_detected(self):
        answer = "As held in AIR 1973 SC 1461, the offence is complete on inducement."
        report = verify_answer(answer, CONTEXT)
        assert not report.grounded
        assert any(c.kind == "citation" for c in report.unsupported)

    def test_fabricated_case_name_detected(self):
        answer = "The Supreme Court in Kesavananda Bharati v. State of Kerala considered this."
        report = verify_answer(answer, CONTEXT)
        assert not report.grounded
        assert any(c.kind == "case_name" for c in report.unsupported)

    def test_fabricated_punishment_detected(self):
        answer = "The punishment is imprisonment for a term which may extend to ten years."
        report = verify_answer(answer, CONTEXT)
        assert not report.grounded
        assert any(c.kind == "punishment" for c in report.unsupported)

    def test_correct_punishment_from_context_passes(self):
        answer = "The provision allows imprisonment for a term which may extend to seven years."
        assert verify_answer(answer, CONTEXT).grounded

    def test_fabricated_statute_name_detected(self):
        answer = "This is governed by the Prevention of Corruption Act."
        report = verify_answer(answer, CONTEXT)
        assert not report.grounded
        assert any(c.kind == "statute" for c in report.unsupported)

    def test_refusal_is_treated_as_grounded(self):
        assert verify_answer(INSUFFICIENT_EVIDENCE, CONTEXT).grounded

    def test_support_rate_is_reported(self):
        answer = "Section 420 is relevant, and so is section 999."
        report = verify_answer(answer, CONTEXT)
        assert report.checked_claims == 2
        assert report.supported_claims == 1
        assert report.support_rate == pytest.approx(0.5)

    def test_annotation_marks_unsupported_claims(self):
        answer = "The punishment extends to ten years."
        report = verify_answer(answer, CONTEXT)
        annotated = annotate_unsupported(answer, report)
        assert "Not supported by the retrieved sources" in annotated
        assert answer in annotated


# ── End-to-end: questions the model "knows" but the corpus does not ────────

# Each of these is answerable from an LLM's pretrained knowledge and is NOT in
# this corpus. Correct behaviour is refusal.
OUT_OF_CORPUS_BUT_KNOWN = [
    pytest.param("What are the grounds for divorce under the Hindu Marriage Act?", id="other-statute"),
    pytest.param("What did the Supreme Court hold in Kesavananda Bharati?", id="absent-case"),
    pytest.param("What is Section 66A of the Information Technology Act?", id="unrelated-act"),
    pytest.param("What is the penalty under Section 138 of the Negotiable Instruments Act?", id="absent-act"),
    pytest.param("What is IPC Section 999?", id="nonexistent-section"),
    pytest.param("What are the 2023 amendments to the Competition Act?", id="recent-development"),
]


@needs_index
class TestOutOfCorpusRetrievalOnly:
    """Without a key the gate still decides — this is the layer that must hold."""

    @pytest.mark.parametrize("query", OUT_OF_CORPUS_BUT_KNOWN)
    def test_gate_decision_is_recorded(self, query):
        result = statute_rag.retrieve(query)
        assert "abstention" in result
        assert isinstance(result["abstention"]["should_abstain"], bool)

    def test_nonexistent_section_is_flagged(self):
        """A query naming a section that does not exist must not look confident."""
        result = statute_rag.retrieve("What is IPC Section 999?")
        signals = result["abstention"]["signals"]
        assert signals["requested_section"] == "999"
        assert signals["requested_section_found"] is False


@needs_index
@needs_llm
class TestOutOfCorpusEndToEnd:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("query", OUT_OF_CORPUS_BUT_KNOWN)
    async def test_refuses_rather_than_recalling(self, query):
        result = await statute_rag.answer(query)
        assert result["abstained"] or result["grounded"], (
            f"answered {query!r} without grounded evidence — pretrained knowledge leaked"
        )

    @pytest.mark.asyncio
    async def test_never_cites_an_unretrieved_section(self):
        result = await statute_rag.answer("What is the punishment for murder?")
        if result["llm_used"] and not result["abstained"]:
            retrieved = {(s["law"], s["section"]) for s in result["sources"]}
            for citation in result["cited_sections"]:
                assert (citation["law"], citation["section"]) in retrieved

    @pytest.mark.asyncio
    async def test_mixed_ipc_bns_question_does_not_conflate(self):
        result = await statute_rag.answer(
            "Is IPC Section 302 the same as BNS Section 103?"
        )
        assert result["abstained"] or result["grounded"]

    @pytest.mark.asyncio
    async def test_response_carries_grounding_report(self):
        result = await statute_rag.answer("What does IPC Section 420 deal with?")
        assert "grounding" in result
        assert "support_rate" in result["grounding"]
