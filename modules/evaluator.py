"""
Module 10 — Evaluation Module
Logs per-query metrics to JSONL and prints session summaries.
"""

import json
import logging
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

from tabulate import tabulate

import config

logger = logging.getLogger(__name__)


# ── Hallucination Heuristic ───────────────────────────────────────────────────

def _token_overlap(text_a: str, text_b: str) -> float:
    """Rough Jaccard token overlap between two texts."""
    tokens_a = set(text_a.lower().split())
    tokens_b = set(text_b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def hallucination_check(answer: str, chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Heuristic hallucination check: measure overlap of the answer
    against the retrieved context.

    Returns
    -------
    dict with 'max_overlap', 'avg_overlap', 'likely_hallucination' (bool)
    """
    if not chunks or not answer:
        return {"max_overlap": 0.0, "avg_overlap": 0.0, "likely_hallucination": True}

    context_text = " ".join(c.get("text", "") for c in chunks)
    overlaps = [_token_overlap(answer, c.get("text", "")) for c in chunks]

    max_ov = max(overlaps)
    avg_ov = sum(overlaps) / len(overlaps)

    # Flag as possible hallucination if max overlap < 5%
    likely_hallucination = max_ov < 0.05

    return {
        "max_overlap":          round(max_ov, 4),
        "avg_overlap":          round(avg_ov, 4),
        "likely_hallucination": likely_hallucination,
    }


# ── Evaluator ─────────────────────────────────────────────────────────────────

class Evaluator:
    """
    Logs per-query metrics and prints a session summary on exit.

    Metrics logged per query
    ------------------------
    - query
    - latency_ms
    - retrieved_count
    - reranked_count
    - top_reranker_score
    - max_overlap          (hallucination proxy)
    - avg_overlap
    - likely_hallucination
    - timestamp
    """

    def __init__(self, log_path: Path = config.EVAL_LOG_PATH):
        self._log_path = Path(log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._session_records: List[Dict[str, Any]] = []
        self._session_start = time.time()

    def log(
        self,
        query:          str,
        result:         Dict[str, Any],
        reranked_chunks: List[Dict[str, Any]],
    ) -> None:
        """
        Compute and persist metrics for a single query.

        Parameters
        ----------
        query           : the user's question
        result          : dict returned by QueryEngine.query()
        reranked_chunks : the final chunks passed to the LLM (for hallucination check)
        """
        hall = hallucination_check(result.get("answer", ""), reranked_chunks)

        top_score = (
            reranked_chunks[0].get("_reranker_score", 0.0)
            if reranked_chunks else 0.0
        )

        record = {
            "query":               query,
            "latency_ms":          result.get("latency_ms", 0),
            "retrieved_count":     result.get("retrieved_count", 0),
            "reranked_count":      result.get("reranked_count", 0),
            "top_reranker_score":  round(top_score, 4),
            "max_overlap":         hall["max_overlap"],
            "avg_overlap":         hall["avg_overlap"],
            "likely_hallucination": hall["likely_hallucination"],
            "timestamp":           time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        # Append to JSONL file
        with open(self._log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")

        self._session_records.append(record)
        logger.debug(f"[Evaluator] Logged metrics for: {query[:60]}")

    def print_summary(self) -> None:
        """Print a formatted session summary table to stdout."""
        if not self._session_records:
            print("[Evaluator] No queries evaluated this session.")
            return

        elapsed = time.time() - self._session_start
        n = len(self._session_records)

        avg_latency   = sum(r["latency_ms"]   for r in self._session_records) / n
        avg_retrieved = sum(r["retrieved_count"] for r in self._session_records) / n
        avg_overlap   = sum(r["avg_overlap"]   for r in self._session_records) / n
        hall_count    = sum(1 for r in self._session_records if r["likely_hallucination"])

        rows = [
            ["Queries answered",       n],
            ["Session duration",       f"{elapsed:.1f}s"],
            ["Avg latency",            f"{avg_latency:.0f} ms"],
            ["Avg retrieved chunks",   f"{avg_retrieved:.1f}"],
            ["Avg answer-context overlap", f"{avg_overlap:.2%}"],
            ["Possible hallucinations", f"{hall_count} / {n}  "
                f"({hall_count/n:.0%})"],
            ["Log file",               str(self._log_path)],
        ]

        print("\n" + "═" * 50)
        print("  LEGAL RAG — SESSION SUMMARY")
        print("═" * 50)
        print(tabulate(rows, tablefmt="simple"))
        print("═" * 50 + "\n")

    def load_history(self) -> List[Dict[str, Any]]:
        """Return all historical records from the JSONL log."""
        if not self._log_path.exists():
            return []
        records = []
        with open(self._log_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return records
