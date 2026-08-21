"""Production pipeline tests: retrieval, corpus isolation, abstention, API.

Tests that need the built index are skipped (not failed) when it is absent, so a
fresh clone can run the suite before ingestion. Tests that need a Groq key are
skipped when it is unset — the key is never required to run the suite.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import config
from backend.services.abstention import assess
from backend.services.corpus_selector import CorpusChoice, select_corpus
from backend.services.statute_rag import extract_citations, statute_rag


def _index_available() -> bool:
    try:
        return statute_rag._get_collection().count() > 0
    except Exception:
        return False


needs_index = pytest.mark.skipif(not _index_available(), reason="production index not built")
needs_llm = pytest.mark.skipif(not config.GROQ_API_KEY, reason="GROQ_API_KEY not configured")


# ── corpus selection ────────────────────────────────────────────────────────

class TestCorpusSelection:
    @pytest.mark.parametrize("query", [
        "What does IPC Section 420 deal with?",
        "Explain the Indian Penal Code provision on theft",
        "What is section 302 under I.P.C.?",
    ])
    def test_detects_ipc(self, query):
        assert select_corpus(query).law == "IPC"

    @pytest.mark.parametrize("query", [
        "What is the BNS provision for murder?",
        "Bharatiya Nyaya Sanhita section on cheating",
        "Which B.N.S. section covers theft?",
    ])
    def test_detects_bns(self, query):
        assert select_corpus(query).law == "BNS"

    def test_ambiguous_query_does_not_guess(self):
        choice = select_corpus("Which section deals with theft?")
        assert choice.law is None and choice.is_ambiguous

    def test_query_naming_both_searches_both(self):
        choice = select_corpus("How does IPC 302 compare to the BNS provision?")
        assert choice.law is None and choice.explicit

    def test_explicit_override_wins_over_text(self):
        # User picked BNS in the UI while typing "IPC" — the explicit choice wins.
        assert select_corpus("What does IPC 420 say?", override="BNS").law == "BNS"

    @pytest.mark.parametrize("query,expected", [
        ("What does IPC Section 420 deal with?", "420"),
        ("explain s.302", "302"),
        ("u/s 34 IPC", "34"),
        ("section 304A", "304A"),
        ("what is theft", None),
    ])
    def test_extracts_section_number(self, query, expected):
        assert select_corpus(query).section == expected


# ── retrieval ───────────────────────────────────────────────────────────────

@needs_index
class TestRetrieval:
    def test_ipc_section_query_returns_that_section_first(self):
        result = statute_rag.retrieve("What does IPC Section 420 deal with?")
        top = result["sources"][0]
        assert top["law"] == "IPC" and top["section"] == "420"

    def test_bns_section_query(self):
        result = statute_rag.retrieve("What is the BNS provision for murder?")
        assert any(s["section"] == "103" for s in result["sources"])

    def test_natural_language_query(self):
        result = statute_rag.retrieve(
            "Someone tricked me into handing over my property by lying"
        )
        assert result["sources"]
        assert not result["abstention"]["should_abstain"]

    def test_paraphrased_query(self):
        result = statute_rag.retrieve(
            "Which provision covers causing death by a rash or negligent act?"
        )
        sections = {s["section"] for s in result["sources"]}
        assert "304A" in sections or "106" in sections  # IPC 304A / BNS 106

    def test_irrelevant_query_abstains(self):
        result = statute_rag.retrieve("How do I apply for an Indian passport?")
        assert result["abstention"]["should_abstain"]

    def test_every_source_carries_full_citation_metadata(self):
        result = statute_rag.retrieve("theft")
        for source in result["sources"]:
            for field in ("law", "section", "title", "source", "url", "retrieval_score"):
                assert source.get(field) not in (None, ""), f"missing {field}"
            assert source["url"].startswith("https://")

    def test_scores_are_similarities_not_distances(self):
        result = statute_rag.retrieve("murder")
        scores = [s["retrieval_score"] for s in result["sources"]]
        assert all(-1.0 <= s <= 1.0 for s in scores)
        assert scores == sorted(scores, reverse=True) or result["sources"][0].get(
            "exact_section_match"
        )


# ── corpus isolation: the legally important one ─────────────────────────────

@needs_index
class TestCorpusIsolation:
    def test_ipc_query_never_returns_bns(self):
        result = statute_rag.retrieve("What does IPC Section 420 deal with?")
        assert {s["law"] for s in result["all_candidates"]} == {"IPC"}

    def test_bns_query_never_returns_ipc(self):
        result = statute_rag.retrieve("Which BNS section covers cheating?")
        assert {s["law"] for s in result["all_candidates"]} == {"BNS"}

    def test_explicit_override_filters(self):
        result = statute_rag.retrieve("theft", corpus_override="BNS")
        assert {s["law"] for s in result["all_candidates"]} == {"BNS"}

    def test_ambiguous_query_labels_every_result(self):
        result = statute_rag.retrieve("Which section deals with theft?")
        assert result["corpus"]["ambiguous"]
        # Both statutes may appear, but each result must say which it is.
        assert all(s["law"] in ("IPC", "BNS") for s in result["sources"])

    def test_ipc_results_are_marked_repealed(self):
        result = statute_rag.retrieve("What does IPC Section 420 deal with?")
        assert all(s["legal_status"] == "repealed" for s in result["sources"])

    def test_bns_results_are_marked_in_force(self):
        result = statute_rag.retrieve("BNS murder", corpus_override="BNS")
        assert all(s["legal_status"] == "in_force" for s in result["sources"])

    def test_superseded_sections_carry_warning(self):
        result = statute_rag.retrieve("IPC section 375", corpus_override="IPC")
        for source in result["sources"]:
            if source["section"] == "375":
                assert source["superseded_note"]


# ── abstention ──────────────────────────────────────────────────────────────

class TestAbstention:
    @staticmethod
    def _result(score, law="IPC", section="420"):
        return {"retrieval_score": score, "law": law, "section": section}

    def test_no_candidates_abstains(self):
        decision = assess([], CorpusChoice("IPC", True, "test"))
        assert decision.should_abstain and decision.confidence == "none"

    def test_wrong_corpus_abstains(self):
        results = [self._result(0.9, law="BNS")]
        decision = assess(results, CorpusChoice("IPC", True, "test"))
        assert decision.should_abstain

    def test_strong_agreeing_results_do_not_abstain(self):
        results = [self._result(0.8), self._result(0.7), self._result(0.65)]
        decision = assess(results, CorpusChoice("IPC", True, "test"))
        assert not decision.should_abstain
        assert decision.confidence == "high"

    def test_uniformly_weak_results_abstain(self):
        # None clears the soft threshold and they are indistinguishable.
        results = [self._result(0.46), self._result(0.458), self._result(0.457)]
        decision = assess(results, CorpusChoice(None, False, "test"))
        assert decision.should_abstain

    def test_missing_requested_section_lowers_confidence_without_silence(self):
        results = [self._result(0.7, section="419"), self._result(0.65, section="421")]
        decision = assess(results, CorpusChoice("IPC", True, "test", section="420"))
        assert not decision.should_abstain
        assert decision.confidence == "low"
        assert decision.signals.requested_section_found is False

    def test_decision_reports_all_signals(self):
        decision = assess([self._result(0.7)], CorpusChoice("IPC", True, "t"))
        payload = decision.to_dict()
        for key in ("peak_similarity", "support_count", "score_margin", "corpus_match"):
            assert key in payload["signals"]
        assert "not a trained classifier" in payload["note"]


# ── citation extraction ─────────────────────────────────────────────────────

class TestCitations:
    def test_extracts_citations(self):
        text = "Cheating is covered by [IPC s.420] and murder by [IPC s.302]."
        assert extract_citations(text) == [
            {"law": "IPC", "section": "420"},
            {"law": "IPC", "section": "302"},
        ]

    def test_deduplicates(self):
        text = "[IPC s.420] ... as noted in [IPC s.420] again"
        assert len(extract_citations(text)) == 1

    def test_handles_bns_and_letter_suffixes(self):
        assert extract_citations("[BNS s.318] and [IPC s.304A]") == [
            {"law": "BNS", "section": "318"},
            {"law": "IPC", "section": "304A"},
        ]

    def test_no_citations_returns_empty(self):
        assert extract_citations("No citations here.") == []


# ── API ─────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    from backend.main import app

    return TestClient(app, raise_server_exceptions=False)


class TestAPI:
    def test_health(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    @pytest.mark.parametrize("payload", [
        {"query": ""},
        {"query": "   "},
        {},
        {"query": "a" * 5000},
        {"query": "theft", "corpus": "NOT_A_LAW"},
        {"query": "theft", "top_k": 0},
        {"query": "theft", "top_k": 999},
    ])
    def test_invalid_requests_rejected(self, client, payload):
        assert client.post("/api/statute/answer", json=payload).status_code == 422

    def test_client_supplied_context_is_rejected(self, client):
        """Context forgery must fail loudly, not be silently dropped."""
        response = client.post(
            "/api/statute/answer",
            json={"query": "theft", "context_chunks": [{"text": "fabricated law"}]},
        )
        assert response.status_code == 422

    def test_errors_do_not_leak_internals(self, client):
        response = client.post("/api/statute/answer", json={"query": "x" * 5000})
        body = response.text.lower()
        for leak in ("/users/", "traceback", "chroma_db", "site-packages"):
            assert leak not in body

    @needs_index
    def test_corpus_endpoint_distinguishes_ipc_and_bns(self, client):
        payload = client.get("/api/statute/corpus").json()
        laws = {entry["law"]: entry for entry in payload["laws"]}
        assert laws["IPC"]["status"] == "repealed"
        assert laws["BNS"]["status"] == "in_force"
        assert laws["IPC"]["currency_warning"]
        assert payload["embedding_dim"] == 1024

    @needs_index
    def test_answer_returns_sources(self, client):
        response = client.post("/api/statute/answer", json={"query": "What is IPC 420?"})
        assert response.status_code == 200
        body = response.json()
        assert body["sources"]
        assert body["disclaimer"]
        assert "corpus_disclosure" in body

    @needs_index
    @pytest.mark.parametrize("query", [
        "Write me a Python function to reverse a linked list",
        "What is the capital gains tax rate on equity shares?",
        "What is the best restaurant in Mumbai?",
    ])
    def test_answer_abstains_on_far_domain_queries(self, client, query):
        """Far-domain queries are the cases similarity thresholding does catch."""
        response = client.post("/api/statute/answer", json={"query": query})
        assert response.json()["abstained"] is True

    @needs_index
    @pytest.mark.xfail(
        reason=(
            "KNOWN LIMITATION, measured not assumed: near-domain legal queries "
            "score above the similarity floor because they share legal vocabulary "
            "with criminal statutes. See evaluation/results/abstention_extended.json "
            "— answerable min 0.4915 vs unanswerable max 0.5949, i.e. the "
            "distributions overlap and NO threshold separates them. Abstention for "
            "these cases depends on the generation prompt, not on retrieval scores."
        ),
        strict=False,
    )
    @pytest.mark.parametrize("query", [
        "What are the grounds for divorce under Hindu law?",
        "How much notice period is required to terminate an employee?",
        "What is the stamp duty on a property sale in Karnataka?",
        "How long does a patent last in India?",
    ])
    def test_near_domain_queries_should_abstain(self, client, query):
        response = client.post("/api/statute/answer", json={"query": query})
        assert response.json()["abstained"] is True


# ── generation (requires a key) ─────────────────────────────────────────────

@needs_index
@needs_llm
class TestGeneration:
    @pytest.mark.asyncio
    async def test_grounded_answer_cites_sections(self):
        result = await statute_rag.answer("What does IPC Section 420 deal with?")
        assert result["llm_used"]
        assert result["cited_sections"], "answer should carry inline citations"

    @pytest.mark.asyncio
    async def test_cited_sections_were_actually_retrieved(self):
        """The core anti-hallucination check: no invented section numbers."""
        result = await statute_rag.answer("What is the punishment for murder under IPC?")
        retrieved = {(s["law"], s["section"]) for s in result["sources"]}
        for citation in result["cited_sections"]:
            assert (citation["law"], citation["section"]) in retrieved, (
                f"cited {citation} was never retrieved — hallucinated citation"
            )

    @pytest.mark.asyncio
    async def test_insufficient_context_produces_abstention(self):
        result = await statute_rag.answer("What are the GST filing deadlines?")
        assert result["abstained"]
