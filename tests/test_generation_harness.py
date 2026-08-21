"""
Tests for the generation harness's persistence and model-isolation guarantees.

These exist because a full A-E run was lost once already: the harness completed
configurations A and B against the live API, then died partway through C on the
provider's daily token cap having written nothing. Roughly 197,000 tokens of real
model calls produced zero recoverable data.

The benchmark must now survive being interrupted at any point, and must never
combine results produced by different models — a mixed A-E table would be
meaningless, because a difference between configurations could just be a
difference between models.

Everything here runs against a fake client. No API key, no network, no quota.
"""

from __future__ import annotations

import json

import pytest

import config
from evaluation import evaluate_generation as harness


class FakeCompletions:
    def __init__(self, reply: str):
        self._reply = reply

    async def create(self, **kwargs):
        class _Message:
            content = self._reply

        class _Choice:
            message = _Message()

        class _Response:
            choices = [_Choice()]

        return _Response()


class FakeGroq:
    def __init__(self, reply: str = "IPC Section 302 concerns punishment for murder [1]."):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletions(reply)


@pytest.fixture
def isolated_results(tmp_path, monkeypatch):
    """Point the harness at a temp results file so real artifacts are untouched."""
    out = tmp_path / "generation_experiments.json"
    monkeypatch.setattr(harness, "OUT_PATH", out)
    monkeypatch.setattr(harness, "RESULTS_DIR", tmp_path)
    return out


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeGroq()
    monkeypatch.setattr(harness.statute_rag, "_get_groq", lambda: fake, raising=False)
    monkeypatch.setattr(config, "GROQ_API_KEY", "test-key", raising=False)
    return fake


def _write_saved(path, model: str, configs: dict) -> None:
    path.write_text(json.dumps({
        "model": model,
        "n_questions": 23,
        "complete": False,
        "configurations_present": sorted(configs),
        "configurations": configs,
    }, indent=2))


# ── the eval set must not drift between configurations ─────────────────────

class TestQuestionSetStability:
    def test_question_count_is_23(self):
        assert len(harness.load_questions()) == 23

    def test_question_set_is_deterministic(self):
        first = [q["query"] for q in harness.load_questions()]
        second = [q["query"] for q in harness.load_questions()]
        assert first == second, "question set differs between calls — A-E would not be comparable"

    def test_groups_present(self):
        groups = {q["group"].split("/")[0] for q in harness.load_questions()}
        assert groups == {"answerable", "case_law", "false_premise"}

    def test_answerable_and_unanswerable_split(self):
        questions = harness.load_questions()
        assert sum(1 for q in questions if q["should_answer"]) == 13
        assert sum(1 for q in questions if not q["should_answer"]) == 10


# ── independent persistence ────────────────────────────────────────────────

class TestPersistence:
    def test_partial_returns_empty_when_no_file(self, isolated_results):
        assert harness._load_partial() == {}

    def test_completed_configuration_is_reloaded(self, isolated_results, monkeypatch):
        monkeypatch.setattr(config, "GROQ_MODEL", "openai/gpt-oss-120b", raising=False)
        _write_saved(isolated_results, "openai/gpt-oss-120b", {"A": {"config": "A"}})
        assert set(harness._load_partial()) == {"A"}

    def test_each_configuration_is_saved_separately(self, isolated_results, monkeypatch):
        monkeypatch.setattr(config, "GROQ_MODEL", "openai/gpt-oss-120b", raising=False)
        questions = [{"query": "q", "group": "answerable", "should_answer": True}]

        harness._save({"A": {"config": "A"}}, questions, complete=False)
        assert set(json.loads(isolated_results.read_text())["configurations"]) == {"A"}

        harness._save({"A": {"config": "A"}, "B": {"config": "B"}}, questions, complete=False)
        saved = json.loads(isolated_results.read_text())
        assert set(saved["configurations"]) == {"A", "B"}
        assert saved["configurations_present"] == ["A", "B"]

    def test_incomplete_run_is_flagged(self, isolated_results, monkeypatch):
        monkeypatch.setattr(config, "GROQ_MODEL", "openai/gpt-oss-120b", raising=False)
        harness._save({"A": {}}, [], complete=False)
        assert json.loads(isolated_results.read_text())["complete"] is False

    def test_corrupt_file_does_not_crash_resume(self, isolated_results):
        isolated_results.write_text("{ this is not json")
        assert harness._load_partial() == {}


# ── model isolation: the guarantee that makes A-E comparable ───────────────

class TestModelIsolation:
    def test_results_from_a_different_model_are_not_reused(
        self, isolated_results, monkeypatch
    ):
        """The central guard. Mixing models across configurations would make any
        A-vs-B difference uninterpretable."""
        _write_saved(isolated_results, "openai/gpt-oss-20b", {"A": {"config": "A"}})
        monkeypatch.setattr(config, "GROQ_MODEL", "openai/gpt-oss-120b", raising=False)
        assert harness._load_partial() == {}, (
            "results from gpt-oss-20b were reused for a gpt-oss-120b run"
        )

    def test_same_model_results_are_reused(self, isolated_results, monkeypatch):
        _write_saved(isolated_results, "openai/gpt-oss-120b", {"A": {"config": "A"}})
        monkeypatch.setattr(config, "GROQ_MODEL", "openai/gpt-oss-120b", raising=False)
        assert set(harness._load_partial()) == {"A"}

    def test_saved_file_records_the_model(self, isolated_results, monkeypatch):
        monkeypatch.setattr(config, "GROQ_MODEL", "openai/gpt-oss-120b", raising=False)
        harness._save({"A": {}}, [], complete=False)
        assert json.loads(isolated_results.read_text())["model"] == "openai/gpt-oss-120b"

    def test_missing_model_field_is_not_reused(self, isolated_results, monkeypatch):
        isolated_results.write_text(json.dumps({"configurations": {"A": {}}}))
        monkeypatch.setattr(config, "GROQ_MODEL", "openai/gpt-oss-120b", raising=False)
        assert harness._load_partial() == {}


# ── resume behaviour under failure ─────────────────────────────────────────

class TestResumeUnderFailure:
    @pytest.mark.asyncio
    async def test_failure_persists_earlier_configurations(
        self, isolated_results, fake_llm, monkeypatch
    ):
        """A quota error must not discard configurations that already cost tokens."""
        monkeypatch.setattr(config, "GROQ_MODEL", "openai/gpt-oss-120b", raising=False)
        monkeypatch.setattr(harness, "load_questions", lambda: [
            {"query": "What is IPC Section 302?", "group": "answerable",
             "target": "IPC s.302", "should_answer": True}
        ])

        real_run_config = harness.run_config
        calls = {"n": 0}

        async def flaky(key, questions):
            calls["n"] += 1
            if key == "B":
                raise RuntimeError("simulated provider quota error")
            return await real_run_config(key, questions)

        monkeypatch.setattr(harness, "run_config", flaky)

        with pytest.raises(RuntimeError):
            await harness.run_all(["A", "B"])

        saved = json.loads(isolated_results.read_text())
        assert set(saved["configurations"]) == {"A"}, (
            "configuration A was lost when B failed — the whole point of checkpointing"
        )
        assert saved["complete"] is False

    @pytest.mark.asyncio
    async def test_resume_skips_completed_configurations(
        self, isolated_results, fake_llm, monkeypatch
    ):
        monkeypatch.setattr(config, "GROQ_MODEL", "openai/gpt-oss-120b", raising=False)
        monkeypatch.setattr(harness, "load_questions", lambda: [
            {"query": "What is IPC Section 302?", "group": "answerable",
             "target": "IPC s.302", "should_answer": True}
        ])
        _write_saved(isolated_results, "openai/gpt-oss-120b", {"A": {"config": "A"}})

        ran: list[str] = []
        real_run_config = harness.run_config

        async def tracked(key, questions):
            ran.append(key)
            return await real_run_config(key, questions)

        monkeypatch.setattr(harness, "run_config", tracked)
        await harness.run_all(["A", "B"])

        assert ran == ["B"], f"expected only B to run, ran {ran}"
        saved = json.loads(isolated_results.read_text())
        assert set(saved["configurations"]) == {"A", "B"}

    @pytest.mark.asyncio
    async def test_complete_flag_set_when_all_present(
        self, isolated_results, fake_llm, monkeypatch
    ):
        monkeypatch.setattr(config, "GROQ_MODEL", "openai/gpt-oss-120b", raising=False)
        monkeypatch.setattr(harness, "load_questions", lambda: [
            {"query": "What is IPC Section 302?", "group": "answerable",
             "target": "IPC s.302", "should_answer": True}
        ])
        await harness.run_all(["A"])
        assert json.loads(isolated_results.read_text())["complete"] is True
