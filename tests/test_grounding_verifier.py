"""Adversarial tests for the grounding verifier itself.

The verifier is a safety component, so it needs its own adversarial suite: a
verifier that silently passes fabricated claims is worse than no verifier, since
it produces a "grounded: true" flag that nobody should trust.

Two error directions are tested, and both matter:

  FALSE NEGATIVE  a fabricated claim passes  -> the system asserts something the
                  evidence does not support, while reporting it as grounded
  FALSE POSITIVE  a legitimate paraphrase is rejected -> the system refuses or
                  annotates correct answers, and becomes useless

The paraphrase cases exist specifically to stop the verifier being tightened into
uselessness. A legal answer must be allowed to say "up to seven years" where the
statute says "a term which may extend to seven years".
"""

from __future__ import annotations

import pytest

from backend.services.grounding import verify_answer

# ── contexts ────────────────────────────────────────────────────────────────

PUNISHMENT_CONTEXT = (
    "[1] TYPE: statute | LAW: IPC | SECTION: 420 | TITLE: Cheating\n"
    "TEXT: Whoever cheats shall be punished with imprisonment of either "
    "description for a term which may extend to seven years, and shall also be "
    "liable to fine."
)

CASE_CONTEXT = (
    "[1] TYPE: judgment | COURT: Supreme Court of India | "
    "CASE: Maneka Gandhi versus Union Of India\n"
    "DATE: 25-01-1978 | CITATION: AIR 1978 SC 597\n"
    "TEXT: The Court held that the procedure established by law must be fair, "
    "just and reasonable."
)

SECTION_CONTEXT = (
    "[1] TYPE: statute | LAW: IPC | SECTION: 420 | TITLE: Cheating\n"
    "TEXT: Whoever cheats and thereby dishonestly induces the person deceived to "
    "deliver any property shall be punished."
)


# ── the specified adversarial cases ─────────────────────────────────────────

class TestSpecifiedAdversarialCases:
    def test_altered_punishment_is_unsupported(self):
        """Context says seven years; answer says ten. Must be UNSUPPORTED."""
        report = verify_answer(
            "The imprisonment may extend to ten years.", PUNISHMENT_CONTEXT
        )
        assert not report.grounded
        assert any(c.kind == "punishment" for c in report.unsupported)

    def test_matching_section_is_supported(self):
        """Context says Section 420; answer says Section 420. Must be SUPPORTED."""
        report = verify_answer(
            "This falls under Section 420 of the Code.", SECTION_CONTEXT
        )
        assert report.grounded

    def test_substituted_case_name_is_unsupported(self):
        """Context cites one case; the answer cites a different one."""
        report = verify_answer(
            "This follows the Supreme Court decision in Kesavananda Bharati v. State of Kerala.",
            CASE_CONTEXT,
        )
        assert not report.grounded
        assert any(c.kind == "case_name" for c in report.unsupported)

    def test_case_name_present_in_context_is_supported(self):
        report = verify_answer(
            "The Court in Maneka Gandhi v. Union Of India stated the test.", CASE_CONTEXT
        )
        assert report.grounded


# ── false negatives: fabrications that must not slip through ───────────────

class TestFabricationsAreCaught:
    @pytest.mark.parametrize("answer", [
        "The punishment may extend to ten years.",
        "The punishment may extend to two years.",
        "It carries life imprisonment.",
        "A fine of Rs. 50,000 is prescribed.",
    ])
    def test_wrong_punishment_quantities(self, answer):
        assert not verify_answer(answer, PUNISHMENT_CONTEXT).grounded

    @pytest.mark.parametrize("answer", [
        "See section 511 for attempts.",
        "Section 302 applies here.",
        "This is covered under s. 376.",
    ])
    def test_sections_absent_from_context(self, answer):
        assert not verify_answer(answer, SECTION_CONTEXT).grounded

    @pytest.mark.parametrize("answer", [
        "As held in AIR 1973 SC 1461.",
        "See (2010) 4 SCC 350.",
        "Reported at [2018] 14 S.C.R. 828.",
        "See 2023 INSC 590.",
    ])
    def test_citations_absent_from_context(self, answer):
        assert not verify_answer(answer, CASE_CONTEXT).grounded

    def test_substring_collision_does_not_mask_fabrication(self):
        """Regression: 'ten' is a substring of 'ex-ten-d'.

        A naive `in` test judged a fabricated "ten years" to be supported by a
        context reading "extend to seven years". Word-boundary matching fixes it,
        and this test pins the fix.
        """
        context = "TEXT: imprisonment for a term which may extend to seven years"
        assert not verify_answer("The term may extend to ten years.", context).grounded

    def test_punishment_word_triggers_check(self):
        """Regression: the detector previously ignored the word 'punishment'."""
        report = verify_answer("The punishment extends to ten years.", PUNISHMENT_CONTEXT)
        assert not report.grounded
        assert report.checked_claims >= 1


# ── false positives: legitimate paraphrase must survive ────────────────────

class TestParaphrasesAreAccepted:
    @pytest.mark.parametrize("answer", [
        "Imprisonment of up to seven years may be imposed.",
        "The offence carries a maximum term of seven years.",
        "A person may be imprisoned for as long as seven years, and fined.",
        "Punishable with imprisonment which may extend to seven years.",
    ])
    def test_reworded_but_accurate_punishment(self, answer):
        report = verify_answer(answer, PUNISHMENT_CONTEXT)
        assert report.grounded, f"legitimate paraphrase rejected: {answer!r}"

    @pytest.mark.parametrize("answer", [
        "The provision concerns cheating and dishonest inducement.",
        "It applies where a person is deceived into delivering property.",
        "The section addresses dishonestly obtaining property by deception.",
    ])
    def test_semantic_restatement_without_specifics(self, answer):
        assert verify_answer(answer, SECTION_CONTEXT).grounded

    def test_refusal_is_grounded(self):
        from backend.services.grounding import INSUFFICIENT_EVIDENCE

        assert verify_answer(INSUFFICIENT_EVIDENCE, SECTION_CONTEXT).grounded

    def test_empty_answer_is_grounded(self):
        assert verify_answer("", SECTION_CONTEXT).grounded


# ── documented limitations ─────────────────────────────────────────────────

class TestKnownLimitations:
    """What the verifier provably does NOT catch.

    These are xfail rather than absent so the limitation is visible in test
    output rather than buried in prose. If a future change makes one pass, the
    xpass is a signal to update the documentation.
    """

    @pytest.mark.xfail(
        reason=(
            "LIMITATION: the verifier is lexical, not entailment-based. It checks "
            "that legal specifics appear in the context; it cannot detect a "
            "semantically wrong claim built entirely from words that ARE present. "
            "Detecting this needs an NLI model or a second LLM pass."
        ),
        strict=False,
    )
    def test_semantic_inversion_using_only_context_words(self):
        # Every term appears in the context, but the meaning is reversed.
        answer = "A person who cheats shall not be punished with imprisonment."
        assert not verify_answer(answer, PUNISHMENT_CONTEXT).grounded

    @pytest.mark.xfail(
        reason=(
            "LIMITATION: an unsupported claim carrying no verifiable specific — "
            "no section, citation, case name, statute or quantity — presents no "
            "surface for a lexical check to grip."
        ),
        strict=False,
    )
    def test_vague_unsupported_legal_assertion(self):
        answer = "Courts generally take a lenient view in such matters."
        assert not verify_answer(answer, PUNISHMENT_CONTEXT).grounded

    def test_limitations_are_declared_in_the_report(self):
        """The method string must state the limitation, since callers read it."""
        report = verify_answer("Section 420 applies.", SECTION_CONTEXT)
        method = report.to_dict()["method"]
        assert "does NOT detect" in method or "does not detect" in method.lower()


# ── metric plumbing ─────────────────────────────────────────────────────────

class TestSupportRate:
    def test_all_supported(self):
        report = verify_answer("Section 420 applies.", SECTION_CONTEXT)
        assert report.support_rate == 1.0

    def test_partial_support(self):
        report = verify_answer("Section 420 and section 511 apply.", SECTION_CONTEXT)
        assert report.checked_claims == 2
        assert report.support_rate == pytest.approx(0.5)

    def test_no_claims_is_full_support(self):
        report = verify_answer("The provision is explained above.", SECTION_CONTEXT)
        assert report.checked_claims == 0
        assert report.support_rate == 1.0
