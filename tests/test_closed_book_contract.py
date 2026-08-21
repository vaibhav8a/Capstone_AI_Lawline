"""
Regression tests for the closed-book contract.

The claim under test: **an answer cannot be built from a statute or judgment that
retrieval did not return.**

That claim is enforced at three points, and each is tested separately so a
regression in any one of them fails loudly rather than being masked by the
others:

    1. PAYLOAD    the request sent to Groq contains the retrieved evidence and
                  nothing else — no corpus access, no application state, no
                  legal content baked into the prompt
    2. GATE       when retrieval returns nothing usable, no request is sent at all
    3. VERIFIER   an answer citing something absent from the context is caught

These run without a GROQ_API_KEY by substituting a fake client that records what
it was sent. That is deliberate: the contract is a property of the code, not of
the model, and it must be testable in CI with no credentials and no network.
"""

from __future__ import annotations

import json

import pytest

import config
from backend.services.grounding import (
    CLOSED_BOOK_SYSTEM_PROMPT,
    INSUFFICIENT_EVIDENCE,
    verify_answer,
)
from backend.services.statute_rag import statute_rag


def _index_available() -> bool:
    try:
        return statute_rag._get_collection().count() > 0
    except Exception:
        return False


needs_index = pytest.mark.skipif(not _index_available(), reason="production index not built")


# ── a Groq stand-in that records the payload ────────────────────────────────

class RecordingCompletions:
    def __init__(self, recorder, reply):
        self._recorder = recorder
        self._reply = reply

    async def create(self, **kwargs):
        self._recorder.append(kwargs)

        class _Message:
            content = self._reply

        class _Choice:
            message = _Message()

        class _Response:
            choices = [_Choice()]

        return _Response()


class RecordingGroq:
    """Captures every request instead of calling the API."""

    def __init__(self, reply: str = "Section 420 concerns cheating [1]."):
        self.calls: list[dict] = []
        self.chat = type("Chat", (), {})()
        self.chat.completions = RecordingCompletions(self.calls, reply)


@pytest.fixture
def recording_llm(monkeypatch):
    """Install a fake Groq client and pretend a key is configured."""
    fake = RecordingGroq()
    monkeypatch.setattr(statute_rag, "_groq", fake, raising=False)
    monkeypatch.setattr(statute_rag, "_get_groq", lambda: fake, raising=False)
    monkeypatch.setattr(config, "GROQ_API_KEY", "test-key-not-real", raising=False)
    yield fake
    statute_rag._groq = None


def _payload_text(call: dict) -> str:
    return "\n".join(message["content"] for message in call["messages"])


# ── 1. the payload carries only retrieved evidence ─────────────────────────

@needs_index
class TestPayloadContainsOnlyRetrievedEvidence:
    @pytest.mark.asyncio
    async def test_every_section_in_payload_was_retrieved(self, recording_llm):
        """No section may appear in the prompt unless retrieval returned it.

        This is the core of the contract. If a section the retriever never
        returned reached the model, the model could answer from it.
        """
        result = await statute_rag.answer("What does IPC Section 420 deal with?")
        assert recording_llm.calls, "no LLM call was recorded"

        retrieved = {(s["law"], str(s["section"]).upper()) for s in result["sources"]}
        payload = _payload_text(recording_llm.calls[0])

        import re

        # Every "SECTION: <n>" header block in the context must correspond to a
        # source the caller was given.
        for law, section in re.findall(r"LAW: (\w+) \| SECTION: (\S+)", payload):
            assert (law, section.upper()) in retrieved, (
                f"{law} s.{section} appears in the Groq payload but was not retrieved"
            )

    @pytest.mark.asyncio
    async def test_payload_carries_no_unretrieved_statutory_text(self, recording_llm):
        result = await statute_rag.answer("What does IPC Section 420 deal with?")
        payload = _payload_text(recording_llm.calls[0])

        retrieved_text = " ".join(s.get("text", "") for s in result["sources"])
        # A provision the retriever did not return must not be quoted. s.302's
        # distinctive wording is a good canary: it is not in the s.420 result set.
        if "punished with death" not in retrieved_text:
            assert "punished with death" not in payload

    @pytest.mark.asyncio
    async def test_system_prompt_contains_no_legal_content(self, recording_llm):
        """The instructions must not themselves teach the model any law."""
        await statute_rag.answer("What does IPC Section 420 deal with?")
        system = recording_llm.calls[0]["messages"][0]["content"]
        assert system == CLOSED_BOOK_SYSTEM_PROMPT

        lowered = system.lower()
        # Naming the statutes as scope is fine; stating their CONTENT is not.
        for leak in ("whoever cheats", "punished with death", "imprisonment for life",
                     "section 420", "section 302", "murder is", "theft is"):
            assert leak not in lowered, f"system prompt leaks legal content: {leak!r}"

    @pytest.mark.asyncio
    async def test_payload_has_exactly_two_messages(self, recording_llm):
        """System + user only: no history, no application state, no extras."""
        await statute_rag.answer("What does IPC Section 420 deal with?")
        messages = recording_llm.calls[0]["messages"]
        assert len(messages) == 2
        assert [m["role"] for m in messages] == ["system", "user"]

    @pytest.mark.asyncio
    async def test_payload_leaks_no_configuration(self, recording_llm):
        await statute_rag.answer("What does IPC Section 420 deal with?")
        payload = _payload_text(recording_llm.calls[0])
        for secret in (str(config.CHROMA_PERSIST_PATH), config.STATUTE_COLLECTION,
                       str(config.BASE_DIR)):
            assert secret not in payload

    @pytest.mark.asyncio
    async def test_temperature_is_zero(self, recording_llm):
        """Sampling adds ungrounded variation to a task that must not vary."""
        await statute_rag.answer("What does IPC Section 420 deal with?")
        assert recording_llm.calls[0]["temperature"] == 0.0


# ── 2. no retrieval, no request ────────────────────────────────────────────

@needs_index
class TestGateBlocksBeforeAnyRequest:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("query", [
        "Write me a Python function to reverse a linked list",
        "What is the capital gains tax rate on equity shares?",
        "What is the best restaurant in Mumbai?",
    ])
    async def test_llm_is_never_invoked_without_evidence(self, recording_llm, query):
        """The decisive test: the model is unreachable, not merely instructed."""
        result = await statute_rag.answer(query)
        assert result["abstained"] is True
        assert result["sources"] == []
        assert result["llm_used"] is False
        assert recording_llm.calls == [], (
            "Groq was called despite insufficient evidence — the model could have "
            "answered from pretrained knowledge"
        )

    @pytest.mark.asyncio
    async def test_refusal_text_is_the_fixed_sentence(self, recording_llm):
        result = await statute_rag.answer("How do I renew my passport in Canada?")
        if result["abstained"]:
            assert result["answer"] == INSUFFICIENT_EVIDENCE


# ── 3. fabrication in the reply is caught ──────────────────────────────────

@needs_index
class TestFabricatedOutputIsCaught:
    @pytest.mark.asyncio
    async def test_answer_citing_unretrieved_section_is_not_marked_grounded(
        self, monkeypatch
    ):
        """Simulate a model that answers from memory."""
        fabricating = RecordingGroq(
            reply="This is governed by Section 511 and carries ten years, per AIR 1973 SC 1461."
        )
        monkeypatch.setattr(statute_rag, "_get_groq", lambda: fabricating, raising=False)
        monkeypatch.setattr(config, "GROQ_API_KEY", "test-key-not-real", raising=False)

        result = await statute_rag.answer("What does IPC Section 420 deal with?")
        assert result["grounded"] is False, "fabricated claims were reported as grounded"
        kinds = {c["kind"] for c in result["grounding"]["unsupported_claims"]}
        assert kinds & {"section", "citation", "punishment"}
        statute_rag._groq = None

    @pytest.mark.asyncio
    async def test_retry_is_attempted_before_accepting_unsupported_output(
        self, monkeypatch
    ):
        fabricating = RecordingGroq(reply="Section 999 applies here.")
        monkeypatch.setattr(statute_rag, "_get_groq", lambda: fabricating, raising=False)
        monkeypatch.setattr(config, "GROQ_API_KEY", "test-key-not-real", raising=False)

        result = await statute_rag.answer("What does IPC Section 420 deal with?")
        assert result["generation_attempts"] == 2, "no stricter retry was attempted"
        statute_rag._groq = None


# ── 4. the verifier's own contract, corpus-independent ─────────────────────

class TestVerifierRejectsUnretrievedEvidence:
    CONTEXT = (
        "[1] TYPE: statute | LAW: IPC | SECTION: 420 | TITLE: Cheating\n"
        "TEXT: Whoever cheats shall be punished with imprisonment which may "
        "extend to seven years."
    )

    def test_unretrieved_section_rejected(self):
        assert not verify_answer("See also Section 302.", self.CONTEXT).grounded

    def test_unretrieved_case_rejected(self):
        assert not verify_answer(
            "Applied in Bachan Singh v. State of Punjab.", self.CONTEXT
        ).grounded

    def test_unretrieved_citation_rejected(self):
        assert not verify_answer("Reported at (2010) 4 SCC 350.", self.CONTEXT).grounded

    def test_retrieved_content_accepted(self):
        assert verify_answer(
            "Cheating may attract up to seven years' imprisonment.", self.CONTEXT
        ).grounded


# ── 5. judgment evidence obeys the same rule ───────────────────────────────

@needs_index
class TestJudgmentEvidenceIsAlsoConstrained:
    def test_context_builder_emits_only_supplied_sources(self):
        """build_context must not reach into the corpus for extra material."""
        sources = [{
            "source_type": "judgment",
            "case_name": "TEST v. STATE",
            "court": "Supreme Court of India",
            "judgment_date": "01-01-2020",
            "citation": "[2020] 1 S.C.R. 1",
            "sections_referred": ["302"],
            "text": "The court considered the evidence.",
        }]
        context = statute_rag.build_context(sources)
        assert "TEST v. STATE" in context
        assert "The court considered the evidence." in context
        # Nothing from the real corpus should appear.
        assert "Whoever cheats" not in context
        assert context.count("TYPE:") == 1

    def test_context_length_scales_only_with_supplied_sources(self):
        one = statute_rag.build_context([
            {"law": "IPC", "section": "420", "title": "Cheating", "text": "x" * 100}
        ])
        two = statute_rag.build_context([
            {"law": "IPC", "section": "420", "title": "Cheating", "text": "x" * 100},
            {"law": "IPC", "section": "415", "title": "Cheating", "text": "y" * 100},
        ])
        assert len(two) > len(one)
        assert "z" not in one
