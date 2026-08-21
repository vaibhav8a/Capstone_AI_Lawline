"""
evaluate_retrieval.py — controlled retrieval experiments.

Runs a fixed evaluation set against several retrieval configurations that differ
in exactly one dimension at a time, so any difference in the metrics is
attributable to that dimension.

Configurations
--------------
    A  baseline_fixed_window   dense vector only, boundary-unaware chunking
    B  vector_section_whole    dense vector only, one chunk per section
    B2 vector_section_split    dense vector only, section-aware with length cap
    C  hybrid_rrf              BM25 + dense, fused with Reciprocal Rank Fusion
    D  hybrid_rerank           C, re-ordered by a cross-encoder

A→B isolates chunking. B→C isolates hybrid retrieval. C→D isolates reranking.
The embedding-model comparison re-runs configuration B under each model.

Everything written under evaluation/results/ is produced by an actual run; there
are no placeholder numbers anywhere in this file.

Usage
-----
    python -m evaluation.evaluate_retrieval --configs all
    python -m evaluation.evaluate_retrieval --configs B --models bge-base,bge-m3
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from backend.ingestion.build_index import (  # noqa: E402
    EMBEDDING_MODELS,
    collection_name,
    get_model,
)
from backend.ingestion.chunk_statutes import build as build_chunks  # noqa: E402
from evaluation.metrics_ir import (  # noqa: E402
    aggregate,
    dedupe_sections,
    evaluate_query,
    latency_summary,
)

logger = logging.getLogger(__name__)

RESULTS_DIR = config.BASE_DIR / "evaluation" / "results"
TEST_QUERIES = config.BASE_DIR / "evaluation" / "test_queries.json"

CANDIDATE_DEPTH = 50   # candidates pulled from each retriever before fusion
RRF_K = 60             # RRF damping constant, matching the app's config
RERANK_DEPTH = 20      # candidates handed to the cross-encoder
METRIC_KS = (1, 3, 5, 10)


@dataclass
class RetrievalConfig:
    key: str
    label: str
    strategy: str
    use_bm25: bool = False
    use_rerank: bool = False
    description: str = ""
    extras: dict = field(default_factory=dict)


CONFIGS: dict[str, RetrievalConfig] = {
    "A": RetrievalConfig(
        "A", "Baseline (dense, fixed-window chunks)", "fixed_window",
        description="Dense retrieval over boundary-unaware fixed windows.",
    ),
    "B": RetrievalConfig(
        "B", "Dense + section chunking", "section_whole",
        description="Dense retrieval where each chunk is exactly one statutory section.",
    ),
    "B2": RetrievalConfig(
        "B2", "Dense + section-split chunking", "section_split",
        description="Dense retrieval with long sections split at sentence boundaries.",
    ),
    "C": RetrievalConfig(
        "C", "Hybrid BM25 + dense (RRF)", "section_whole", use_bm25=True,
        description="BM25 and dense candidates fused with Reciprocal Rank Fusion.",
    ),
    "D": RetrievalConfig(
        "D", "Hybrid + cross-encoder rerank", "section_whole", use_bm25=True, use_rerank=True,
        description="Hybrid candidates re-ordered by a cross-encoder.",
    ),
}


# ── retrieval backends ──────────────────────────────────────────────────────

class DenseRetriever:
    def __init__(self, strategy: str, model_key: str, corpus: str = "statutes"):
        import chromadb
        from chromadb.config import Settings

        client = chromadb.PersistentClient(
            path=str(config.CHROMA_PERSIST_PATH),
            settings=Settings(anonymized_telemetry=False),
        )
        self.name = collection_name(corpus, strategy, model_key)
        self.collection = client.get_collection(self.name)
        self.model_key = model_key
        self.prefix = EMBEDDING_MODELS[model_key]["query_prefix"]
        self.count = self.collection.count()
        if self.count == 0:
            raise RuntimeError(
                f"collection {self.name} is empty — run: "
                f"python -m backend.ingestion.build_index --strategy {strategy} --model {model_key}"
            )

    def encode(self, query: str):
        model = get_model(self.model_key)
        return model.encode(
            self.prefix + query,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )

    def search(self, query: str, top_k: int) -> list[tuple[tuple[str, str], float]]:
        vector = self.encode(query)
        result = self.collection.query(
            query_embeddings=[vector.tolist()],
            n_results=min(top_k, self.count),
            include=["metadatas", "distances"],
        )
        metadatas = result["metadatas"][0]
        distances = result["distances"][0]
        out = []
        for meta, distance in zip(metadatas, distances):
            # Chroma returns cosine *distance*; convert to a similarity score.
            out.append(((meta["document"], meta["section"]), 1.0 - float(distance)))
        return out


class BM25Retriever:
    def __init__(self, strategy: str):
        from rank_bm25 import BM25Okapi

        self.chunks = build_chunks(strategy)
        tokenised = [self._tokenise(c["text"]) for c in self.chunks]
        self.index = BM25Okapi(tokenised)

    @staticmethod
    def _tokenise(text: str) -> list[str]:
        import re

        return re.findall(r"[a-z0-9]+", text.lower())

    def search(self, query: str, top_k: int) -> list[tuple[tuple[str, str], float]]:
        scores = self.index.get_scores(self._tokenise(query))
        ordered = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        return [
            ((self.chunks[i]["document"], self.chunks[i]["section"]), float(scores[i]))
            for i in ordered
        ]


_RERANKER = None


def get_reranker():
    global _RERANKER
    if _RERANKER is None:
        from sentence_transformers import CrossEncoder
        import torch

        device = "cuda" if torch.cuda.is_available() else (
            "mps" if torch.backends.mps.is_available() else "cpu"
        )
        logger.info("[rerank] loading %s on %s", config.RERANKER_MODEL, device)
        _RERANKER = CrossEncoder(config.RERANKER_MODEL, device=device)
    return _RERANKER


def rrf_fuse(
    runs: list[list[tuple[tuple[str, str], float]]], k: int = RRF_K
) -> list[tuple[str, str]]:
    """Reciprocal Rank Fusion over several ranked runs."""
    scores: dict[tuple[str, str], float] = {}
    for run in runs:
        for rank, (key, _score) in enumerate(run, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return [key for key, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)]


# ── experiment driver ───────────────────────────────────────────────────────

def section_text_lookup(strategy: str) -> dict[tuple[str, str], str]:
    lookup: dict[tuple[str, str], str] = {}
    for chunk in build_chunks(strategy):
        key = (chunk["document"], chunk["section"])
        lookup.setdefault(key, chunk["text"])
    return lookup


def run_config(cfg: RetrievalConfig, model_key: str, queries: list[dict]) -> dict:
    dense = DenseRetriever(cfg.strategy, model_key)
    bm25 = BM25Retriever(cfg.strategy) if cfg.use_bm25 else None
    texts = section_text_lookup(cfg.strategy) if cfg.use_rerank else {}

    per_query_rows = []
    scored_metrics = []
    latencies: list[float] = []
    abstention_rows = []

    for item in queries:
        gold = {(g["document"], g["section"]) for g in item["gold"]}

        start = time.perf_counter()
        dense_run = dense.search(item["query"], CANDIDATE_DEPTH)
        runs = [dense_run]
        if bm25 is not None:
            runs.append(bm25.search(item["query"], CANDIDATE_DEPTH))

        if len(runs) == 1:
            ranked = [key for key, _ in dense_run]
        else:
            ranked = rrf_fuse(runs)

        ranked = dedupe_sections(ranked)

        if cfg.use_rerank and ranked:
            head = ranked[:RERANK_DEPTH]
            pairs = [(item["query"], texts.get(key, "")) for key in head]
            scores = get_reranker().predict(pairs)
            order = sorted(range(len(head)), key=lambda i: float(scores[i]), reverse=True)
            ranked = [head[i] for i in order] + ranked[RERANK_DEPTH:]

        latency_ms = (time.perf_counter() - start) * 1000.0
        latencies.append(latency_ms)

        row = {
            "query_id": item["query_id"],
            "category": item["category"],
            "query": item["query"],
            "gold": [f"{d} s.{s}" for d, s in sorted(gold)],
            "retrieved_top10": [f"{d} s.{s}" for d, s in ranked[:10]],
            "latency_ms": round(latency_ms, 2),
        }

        if item["category"] == "out_of_corpus":
            # No relevant section exists, so ranking metrics are undefined. Record
            # the top similarity instead: a usable abstention signal should be low.
            top_score = dense_run[0][1] if dense_run else 0.0
            row["top_dense_similarity"] = round(float(top_score), 4)
            abstention_rows.append(row)
        else:
            metrics = evaluate_query(ranked, gold, METRIC_KS)
            row.update({k: round(v, 4) for k, v in metrics.items()})
            scored_metrics.append(metrics)

        per_query_rows.append(row)

    summary = {k: round(v, 4) for k, v in aggregate(scored_metrics).items()}

    by_category: dict[str, dict] = {}
    for row in per_query_rows:
        if row["category"] == "out_of_corpus":
            continue
        by_category.setdefault(row["category"], []).append(
            {k: row[k] for k in row if k.startswith(("precision", "recall", "hit_rate", "ndcg")) or k == "mrr"}
        )
    category_summary = {
        category: {k: round(v, 4) for k, v in aggregate(rows).items()}
        for category, rows in by_category.items()
    }

    return {
        "config": cfg.key,
        "label": cfg.label,
        "description": cfg.description,
        "chunking_strategy": cfg.strategy,
        "embedding_model": model_key,
        "embedding_hf_id": EMBEDDING_MODELS[model_key]["hf_id"],
        "embedding_dim": EMBEDDING_MODELS[model_key]["dim"],
        "uses_bm25": cfg.use_bm25,
        "uses_reranker": cfg.use_rerank,
        "reranker_model": config.RERANKER_MODEL if cfg.use_rerank else None,
        "collection": dense.name,
        "corpus_chunks": dense.count,
        "n_queries_scored": len(scored_metrics),
        "n_queries_abstention": len(abstention_rows),
        "metrics": summary,
        "metrics_by_category": category_summary,
        "latency": latency_summary(latencies),
        "per_query": per_query_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configs", default=None, help="comma-separated config keys, or 'all'")
    parser.add_argument("--models", default=None, help="comma-separated model keys")
    # Singular aliases so a single run reads naturally:
    #   python -m evaluation.evaluate_retrieval --model bge-m3 --config B
    parser.add_argument("--config", default=None, help="a single config key (alias for --configs)")
    parser.add_argument("--model", default=None, help="a single model key (alias for --models)")
    parser.add_argument("--out", default=None, help="output filename under evaluation/results/")
    args = parser.parse_args()

    if args.configs and args.config:
        parser.error("use --config or --configs, not both")
    if args.models and args.model:
        parser.error("use --model or --models, not both")

    configs_arg = args.configs or args.config or "all"
    models_arg = args.models or args.model or "bge-m3"

    # A single-run invocation must not clobber the full experiment file. Only an
    # explicit --out, or a full sweep, writes to retrieval_experiments.json.
    if args.out:
        out_name = args.out
    elif args.config or args.model:
        out_name = f"run_{configs_arg.replace(',', '-')}_{models_arg.replace(',', '-')}.json"
    else:
        out_name = "retrieval_experiments.json"

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    queries = json.loads(TEST_QUERIES.read_text(encoding="utf-8"))
    keys = list(CONFIGS) if configs_arg == "all" else configs_arg.split(",")
    models = models_arg.split(",")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for model_key in models:
        for key in keys:
            cfg = CONFIGS[key.strip()]
            logger.info("[eval] config %s (%s) with %s", cfg.key, cfg.strategy, model_key)
            results.append(run_config(cfg, model_key.strip(), queries))

    out_path = RESULTS_DIR / out_name
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "test_set": str(TEST_QUERIES.relative_to(config.BASE_DIR)),
        "n_queries_total": len(queries),
        "metric_ks": list(METRIC_KS),
        "candidate_depth": CANDIDATE_DEPTH,
        "rrf_k": RRF_K,
        "rerank_depth": RERANK_DEPTH,
        "runs": results,
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.info("[eval] wrote %s", out_path)

    header = f"{'cfg':4} {'model':10} {'chunking':14} {'P@5':>7} {'R@5':>7} {'MRR':>7} {'nDCG@5':>7} {'p50 ms':>8}"
    print("\n" + header)
    print("-" * len(header))
    for run in results:
        m = run["metrics"]
        print(
            f"{run['config']:4} {run['embedding_model']:10} {run['chunking_strategy']:14} "
            f"{m['precision@5']:7.3f} {m['recall@5']:7.3f} {m['mrr']:7.3f} "
            f"{m['ndcg@5']:7.3f} {run['latency']['p50_ms']:8.1f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
