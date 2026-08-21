"""
evaluate_generation.py — the A-E generation comparison. READY, NOT RUN.

Requires GROQ_API_KEY. Without one this script refuses to produce output rather
than estimating anything: every number it emits comes from a real model call.

    A  baseline          LLM alone, no retrieval          -- how much does the
                                                             model already "know"?
    B  standard_rag      retrieval + permissive prompt    -- ordinary RAG
    C  closed_book       retrieval + closed-book prompt   -- adds the contract
    D  closed_book_gate  C + retrieval gate               -- adds fail-closed
    E  full              D + grounding verifier + retry   -- adds verification

Each configuration answers the SAME questions, so differences are attributable to
the safeguard added at that step. A is the control: it shows what the model
produces with no evidence at all, which is the behaviour the rest of the stack
exists to prevent.

Metrics (all computed from actual outputs, none estimated)
----------------------------------------------------------
    unsupported_claim_rate   fraction of legal specifics absent from the context
    citation_support_rate    cited sections/cases that were actually retrieved
    citation_completeness    answers carrying at least one citation
    groundedness             answers with zero unsupported claims
    abstention_accuracy      correct refusals on unanswerable questions
    false_acceptance         answered when it should have refused
    false_abstention         refused when evidence existed
    latency                  mean / p50 / p95 per configuration
    llm_invocations          how often the model was actually called

Factual correctness is NOT auto-scored. Deciding whether a legal explanation is
correct needs a qualified reader, and an LLM-judged "correctness" number would be
a fabricated metric dressed up as a measurement. Raw outputs are saved so a human
can score them; the field is left null until that happens.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from backend.services.grounding import (  # noqa: E402
    CLOSED_BOOK_SYSTEM_PROMPT,
    INSUFFICIENT_EVIDENCE,
    verify_answer,
)
from backend.services.legal_rag import legal_rag  # noqa: E402
from backend.services.retrieval_gate import evaluate_gate  # noqa: E402
from backend.services.statute_rag import _decision_from, extract_citations, statute_rag  # noqa: E402
from evaluation.metrics_ir import latency_summary  # noqa: E402

logger = logging.getLogger(__name__)
RESULTS_DIR = config.BASE_DIR / "evaluation" / "results"
OUT_PATH = RESULTS_DIR / "generation_experiments.json"

# The permissive prompt configuration B uses. This is what a normal RAG system
# ships with, and it is included precisely so the cost of that permissiveness is
# measurable rather than asserted.
PERMISSIVE_PROMPT = (
    "You are an expert on Indian criminal law. Use the provided context to answer "
    "the user's legal question as helpfully and completely as you can."
)

BASELINE_PROMPT = (
    "You are an expert on Indian criminal law. Answer the user's legal question."
)


def load_questions() -> list[dict]:
    """Answerable, unanswerable and false-premise questions in one set."""
    from evaluation.legal_eval_sets import (
        COMBINED_HELD_OUT,
        FALSE_PREMISE_HELD_OUT,
        JUDGMENT_HELD_OUT,
    )

    questions: list[dict] = []
    for query, statute, section in COMBINED_HELD_OUT:
        questions.append({"query": query, "group": "answerable",
                          "target": f"{statute} s.{section}", "should_answer": True})
    for query, statute, section in JUDGMENT_HELD_OUT:
        questions.append({"query": query, "group": "case_law",
                          "target": f"{statute} s.{section}", "should_answer": True})
    for query, category, why in FALSE_PREMISE_HELD_OUT:
        questions.append({"query": query, "group": f"false_premise/{category}",
                          "target": None, "should_answer": False, "why": why})
    return questions


async def _raw_call(client, system: str, user: str) -> str:
    response = await client.chat.completions.create(
        model=config.GROQ_MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        temperature=0.0,
    )
    return response.choices[0].message.content or ""


async def run_config(key: str, questions: list[dict]) -> dict:
    client = statute_rag._get_groq()
    if client is None:
        raise RuntimeError("GROQ_API_KEY is not configured")

    rows, latencies = [], []
    invocations = 0

    for item in questions:
        query = item["query"]
        start = time.perf_counter()
        context, answer, grounded, report, called = "", "", None, None, False

        if key == "A":
            # No retrieval at all. The control.
            answer = await _raw_call(client, BASELINE_PROMPT, query)
            called = True

        elif key == "B":
            retrieval = legal_rag.retrieve(query)
            evidence = retrieval["statutes"] + retrieval["judgments"]
            context = statute_rag.build_context(evidence)
            answer = await _raw_call(
                client, PERMISSIVE_PROMPT, f"Context:\n{context}\n\nQuestion: {query}"
            )
            called = True

        elif key == "C":
            retrieval = legal_rag.retrieve(query)
            evidence = retrieval["statutes"] + retrieval["judgments"]
            context = statute_rag.build_context(evidence)
            answer = await _raw_call(
                client, CLOSED_BOOK_SYSTEM_PROMPT,
                f"LEGAL CONTEXT:\n{context}\n\nUSER QUESTION:\n{query}\n\n"
                "Answer the user's question using ONLY the supplied LEGAL CONTEXT.",
            )
            called = True

        elif key == "D":
            retrieval = legal_rag.retrieve(query)
            evidence = retrieval["statutes"] + retrieval["judgments"]
            if not retrieval["answerable"] and retrieval.get("premise_problems"):
                answer = INSUFFICIENT_EVIDENCE
            else:
                gate = evaluate_gate(evidence, _decision_from(retrieval["abstention"]))
                if not gate.allow_generation:
                    answer = INSUFFICIENT_EVIDENCE
                else:
                    context = statute_rag.build_context(evidence)
                    answer = await _raw_call(
                        client, CLOSED_BOOK_SYSTEM_PROMPT,
                        f"LEGAL CONTEXT:\n{context}\n\nUSER QUESTION:\n{query}\n\n"
                        "Answer the user's question using ONLY the supplied LEGAL CONTEXT.",
                    )
                    called = True

        elif key == "E":
            result = await legal_rag.answer(query)
            answer = result["answer"]
            grounded = result.get("grounded")
            report = result.get("grounding")
            called = result.get("llm_used", False)
            evidence = result["statutes"] + result["judgments"]
            context = statute_rag.build_context(evidence) if evidence else ""

        latency = (time.perf_counter() - start) * 1000
        latencies.append(latency)
        invocations += called

        if report is None:
            verified = verify_answer(answer, context)
            grounded = verified.grounded
            report = verified.to_dict()

        citations = extract_citations(answer)
        retrieved_keys = set()
        if key != "A":
            retrieval_for_check = legal_rag.retrieve(query)
            retrieved_keys = {
                (s["law"], str(s["section"]).upper())
                for s in retrieval_for_check["statutes"]
            }
        supported = [c for c in citations if (c["law"], c["section"]) in retrieved_keys]

        abstained = INSUFFICIENT_EVIDENCE.lower() in answer.lower() or (
            "do not contain sufficient" in answer.lower()
        )

        rows.append({
            "query": query,
            "group": item["group"],
            "should_answer": item["should_answer"],
            "answer": answer,                      # raw output, saved for human scoring
            "abstained": abstained,
            "llm_invoked": called,
            "grounded": grounded,
            "unsupported_claims": report["unsupported_claims"],
            "checked_claims": report["checked_claims"],
            "citations": citations,
            "supported_citations": supported,
            "latency_ms": round(latency, 2),
            "factual_correctness": None,           # requires a qualified human reader
        })

    answerable = [r for r in rows if r["should_answer"]]
    unanswerable = [r for r in rows if not r["should_answer"]]
    total_claims = sum(r["checked_claims"] for r in rows)
    total_unsupported = sum(len(r["unsupported_claims"]) for r in rows)
    total_citations = sum(len(r["citations"]) for r in rows)
    total_supported = sum(len(r["supported_citations"]) for r in rows)

    return {
        "config": key,
        "n_questions": len(rows),
        "llm_invocations": invocations,
        "unsupported_claim_rate": round(total_unsupported / total_claims, 4) if total_claims else 0.0,
        "citation_support_rate": round(total_supported / total_citations, 4) if total_citations else None,
        "citation_completeness": round(
            sum(1 for r in rows if r["citations"]) / len(rows), 4) if rows else 0.0,
        "groundedness": round(sum(1 for r in rows if r["grounded"]) / len(rows), 4) if rows else 0.0,
        "abstention_accuracy": round(
            sum(1 for r in unanswerable if r["abstained"]) / len(unanswerable), 4
        ) if unanswerable else None,
        "false_acceptance_rate": round(
            sum(1 for r in unanswerable if not r["abstained"]) / len(unanswerable), 4
        ) if unanswerable else None,
        "false_abstention_rate": round(
            sum(1 for r in answerable if r["abstained"]) / len(answerable), 4
        ) if answerable else None,
        "factual_correctness": None,
        "latency": latency_summary([r["latency_ms"] for r in rows]),
        "per_question": rows,
    }


def _load_partial() -> dict:
    """Configurations already completed in an earlier run of the same model."""
    if not OUT_PATH.exists():
        return {}
    try:
        saved = json.loads(OUT_PATH.read_text())
    except json.JSONDecodeError:
        return {}
    if saved.get("model") != config.GROQ_MODEL:
        logger.warning(
            "[gen] existing results were produced by %s, not %s — not reusing them; "
            "configurations must share a model to be comparable",
            saved.get("model"), config.GROQ_MODEL,
        )
        return {}
    return saved.get("configurations", {})


def _save(results: dict, questions: list[dict], complete: bool) -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model": config.GROQ_MODEL,
        "n_questions": len(questions),
        "complete": complete,
        "configurations_present": sorted(results),
        "note": (
            "factual_correctness is null throughout: it requires a qualified human "
            "reader. Raw answers are saved per question so it can be scored later. "
            "No value here is estimated."
        ),
        "configurations": results,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


async def run_all(configs: list[str]) -> dict:
    """Run each configuration, persisting after every one.

    Written after a full run was lost: the first attempt completed A and B and
    died partway through C on the provider's daily token cap, having written
    nothing. Roughly 197k tokens of real model calls produced zero recoverable
    data. A configuration is a natural checkpoint — it is self-contained and
    comparable on its own — so each is saved the moment it finishes, and a later
    invocation skips whatever is already present for the same model.
    """
    questions = load_questions()
    results = _load_partial()
    if results:
        logger.info("[gen] resuming: %s already complete", ", ".join(sorted(results)))

    todo = [k for k in configs if k not in results]
    logger.info("[gen] %d questions x %d configuration(s) to run", len(questions), len(todo))

    for key in todo:
        logger.info("[gen] running configuration %s ...", key)
        try:
            results[key] = await run_config(key, questions)
        except Exception as exc:
            # Persist what succeeded before re-raising; a provider quota error
            # must not discard the configurations that already cost real tokens.
            _save(results, questions, complete=False)
            logger.error("[gen] configuration %s failed: %s", key, exc)
            logger.error("[gen] saved %d completed configuration(s) to %s",
                         len(results), OUT_PATH)
            raise
        _save(results, questions, complete=False)
        logger.info("[gen] configuration %s saved", key)

    complete = all(k in results for k in configs)
    _save(results, questions, complete=complete)
    return {"model": config.GROQ_MODEL, "n_questions": len(questions),
            "complete": complete, "configurations": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configs", default="A,B,C,D,E")
    parser.add_argument("--check", action="store_true", help="verify readiness, run nothing")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    ready = bool(config.GROQ_API_KEY)
    if args.check:
        questions = load_questions()
        print(json.dumps({
            "harness": "ready",
            "groq_key_configured": ready,
            "model": config.GROQ_MODEL,
            "configurations": ["A", "B", "C", "D", "E"],
            "n_questions": len(questions),
            "groups": sorted({q["group"].split("/")[0] for q in questions}),
            "will_run": ready,
            "blocked_reason": None if ready else "GROQ_API_KEY not set",
        }, indent=2))
        return 0

    if not ready:
        print("GROQ_API_KEY is not configured.")
        print("This harness refuses to emit numbers without real model calls.")
        print("Set the key in .env and re-run. Nothing has been written.")
        return 1

    payload = asyncio.run(run_all([c.strip() for c in args.configs.split(",")]))

    header = f"{'cfg':4} {'calls':>6} {'unsup':>8} {'cit-sup':>8} {'ground':>8} {'abst-acc':>9} {'p50 ms':>8}"
    print("\n" + header)
    print("-" * len(header))
    for key, data in payload["configurations"].items():
        print(f"{key:4} {data['llm_invocations']:6} {data['unsupported_claim_rate']:8.3f} "
              f"{(data['citation_support_rate'] or 0):8.3f} {data['groundedness']:8.3f} "
              f"{(data['abstention_accuracy'] or 0):9.3f} {data['latency']['p50_ms']:8.1f}")
    print(f"\nwrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
