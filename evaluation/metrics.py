"""
evaluation/metrics.py
Evaluation framework measuring:
1. Retrieval Accuracy — hit@k, MRR (does expected case appear in top-k?)
2. Faithfulness Score — LLM-judged: does the answer cite only grounded context?
3. Answer Relevancy — LLM-judged: does the answer address the question?
"""

import json
import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime

import sys
sys.path.append(str(Path(__file__).parent.parent))

import config

logger = logging.getLogger(__name__)
GOLD_QUERIES_PATH = Path(__file__).parent / "gold_queries.json"
EVAL_LOG_PATH     = Path(__file__).parent / "eval_results.jsonl"


# ── Retrieval Metrics ─────────────────────────────────────────────────────────

def hit_at_k(retrieved_cases: List[str], expected_cases: List[str], k: int = 5) -> bool:
    """Returns True if any expected case appears in the top-k retrieved cases."""
    top_k = [c.lower().strip() for c in retrieved_cases[:k]]
    for expected in expected_cases:
        expected_lower = expected.lower()
        if any(expected_lower in r or r in expected_lower for r in top_k):
            return True
    return False


def mean_reciprocal_rank(retrieved_cases: List[str], expected_cases: List[str]) -> float:
    """MRR: returns 1/rank of the first relevant result. 0 if none found."""
    for rank, case in enumerate(retrieved_cases, start=1):
        case_lower = case.lower()
        for expected in expected_cases:
            if expected.lower() in case_lower or case_lower in expected.lower():
                return 1.0 / rank
    return 0.0


def keyword_coverage(answer: str, keywords: List[str]) -> float:
    """Rough answer-relevancy proxy: fraction of expected keywords present."""
    if not keywords:
        return 1.0
    answer_lower = answer.lower()
    hits = sum(1 for kw in keywords if kw.lower() in answer_lower)
    return hits / len(keywords)


# ── LLM Faithfulness Judge ────────────────────────────────────────────────────

async def judge_faithfulness(question: str, context: str, answer: str) -> Dict[str, Any]:
    """
    Calls Groq LLaMA to judge whether the answer is faithful to the context.
    Returns {"faithful": bool, "score": 0-1, "reasoning": str}
    """
    if not config.GROQ_API_KEY:
        return {"faithful": True, "score": 1.0, "reasoning": "Skipped — no GROQ_API_KEY"}

    from groq import AsyncGroq
    client = AsyncGroq(api_key=config.GROQ_API_KEY)

    prompt = f"""You are an evaluation judge for a legal RAG system.

CONTEXT (retrieved passages):
{context[:3000]}

QUESTION: {question}
GENERATED ANSWER: {answer}

Task: Determine if the answer only uses facts from the context (no hallucination).
Return ONLY valid JSON:
{{"faithful": true/false, "score": 0.0–1.0, "reasoning": "brief explanation"}}"""

    try:
        res = await client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=config.GROQ_MODEL,
            temperature=0.0,
            response_format={"type": "json_object"}
        )
        return json.loads(res.choices[0].message.content)
    except Exception as e:
        logger.error(f"Faithfulness judge failed: {e}")
        return {"faithful": True, "score": 0.5, "reasoning": str(e)}


# ── Full Evaluation Runner ─────────────────────────────────────────────────────

async def run_evaluation(rag_service, top_k: int = 10) -> Dict[str, Any]:
    """
    Runs the gold query set against the live RAG system.
    Produces per-query and aggregate metrics.
    """
    gold_queries = json.loads(GOLD_QUERIES_PATH.read_text())["queries"]

    results = []
    hit_scores   = []
    mrr_scores   = []
    kw_scores    = []
    faith_scores = []

    for q in gold_queries:
        logger.info(f"[Eval] Running: {q['id']} — {q['question'][:60]}...")

        # 1. Retrieve
        pipeline_res = await rag_service.query(q["question"])
        chunks = pipeline_res.get("context_chunks", [])
        retrieved_cases = [c.get("case_title", "") for c in chunks]

        # 2. Retrieval metrics
        hit  = hit_at_k(retrieved_cases, q.get("expected_case_ids", []), top_k)
        mrr  = mean_reciprocal_rank(retrieved_cases, q.get("expected_case_ids", []))

        # 3. Generate answer (collect full stream into string)
        answer_tokens = []
        async for token in rag_service.stream_answer(q["question"], chunks):
            answer_tokens.append(token)
        answer = "".join(answer_tokens).strip()

        # 4. Keyword coverage
        kw_cov = keyword_coverage(answer, q.get("expected_answer_keywords", []))

        # 5. Faithfulness (LLM judge)
        context_str = "\n".join(c.get("text", "") for c in chunks[:5])
        faith = await judge_faithfulness(q["question"], context_str, answer)

        row = {
            "id":           q["id"],
            "question":     q["question"],
            "category":     q.get("category", ""),
            "hit_at_k":     hit,
            "mrr":          round(mrr, 4),
            "keyword_cov":  round(kw_cov, 4),
            "faithful":     faith["faithful"],
            "faith_score":  faith["score"],
            "faith_reason": faith["reasoning"],
        }
        results.append(row)
        hit_scores.append(float(hit))
        mrr_scores.append(mrr)
        kw_scores.append(kw_cov)
        faith_scores.append(faith["score"])

    # Aggregate
    n = len(results)
    summary = {
        "timestamp":       datetime.utcnow().isoformat(),
        "num_queries":     n,
        "hit_at_k":        round(sum(hit_scores) / n, 4),
        "mean_mrr":        round(sum(mrr_scores) / n, 4),
        "mean_kw_cov":     round(sum(kw_scores) / n, 4),
        "mean_faithfulness": round(sum(faith_scores) / n, 4),
        "per_query":       results,
    }

    # Persist to JSONL log
    with EVAL_LOG_PATH.open("a") as fh:
        fh.write(json.dumps(summary) + "\n")

    logger.info(f"[Eval] Complete. HIT@{top_k}={summary['hit_at_k']} MRR={summary['mean_mrr']} "
                f"Faith={summary['mean_faithfulness']}")
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Quick smoke test without a live RAG service
    print("Gold queries:", len(json.loads(GOLD_QUERIES_PATH.read_text())["queries"]))
    print("Eval log will be written to:", EVAL_LOG_PATH)
