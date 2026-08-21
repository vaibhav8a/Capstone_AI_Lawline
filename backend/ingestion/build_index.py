"""
build_index.py — Steps 6-7 of the ingestion pipeline: embeddings and vector index.

    … Semantic Chunking
            ↓
    >>> Embedding Generation → Vector Index <<<   (this module)

Builds one ChromaDB collection per (corpus, chunking strategy, embedding model)
combination so that retrieval experiments can compare configurations without
rebuilding anything in between. Collections are named:

    exp_<corpus>_<strategy>_<model-slug>

Embedding wall-clock time is recorded per build into
`evaluation/results/index_build_stats.json`, which is the source for the
"embedding generation time" figures in the write-up. Nothing here is estimated.

Usage
-----
    python -m backend.ingestion.build_index --model bge-base --strategy section_whole
    python -m backend.ingestion.build_index --all      # every combination
    python -m backend.ingestion.build_index --verify   # report collection counts
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

import config  # noqa: E402
from backend.ingestion.chunk_statutes import STRATEGIES, build as build_chunks  # noqa: E402

logger = logging.getLogger(__name__)

RESULTS_DIR = config.BASE_DIR / "evaluation" / "results"
BUILD_STATS_PATH = RESULTS_DIR / "index_build_stats.json"

# The two BGE variants under comparison. bge-m3 is what config.py currently
# declares; bge-base-en-v1.5 is what the 768-dim index on disk was actually built
# with. The benchmark decides which one the system should ship.
#
# max_seq_length is pinned to 512 for every model. Two reasons:
#
#  1. Fair comparison. bge-m3 defaults to an 8192-token window and bge-base to
#     512. Leaving those defaults in place would confound "which model embeds
#     legal text better" with "which model saw more of the chunk", so the
#     comparison would not isolate the variable it claims to.
#  2. Memory. At the 8192 default, a batch of long statutory sections asks the
#     MPS backend for a ~19.8 GiB attention buffer and the build dies with
#     "Invalid buffer size". Capping at 512 keeps it under a gigabyte.
#
# Cost of the cap: chunks longer than ~512 tokens are truncated. The corpus
# median is 98 words (~128 tokens), so this affects only the longest sections —
# it is recorded in the build stats as `truncated_chunks` rather than left implicit.
MAX_SEQ_LENGTH = 512

EMBEDDING_MODELS: dict[str, dict] = {
    "bge-base": {
        "hf_id": "BAAI/bge-base-en-v1.5",
        "dim": 768,
        "query_prefix": "Represent this sentence for searching relevant passages: ",
        "batch_size": 32,
        "notes": "English-only, 109M params, ~440MB.",
    },
    "bge-m3": {
        "hf_id": "BAAI/bge-m3",
        "dim": 1024,
        # bge-m3 is trained without an instruction prefix; adding one degrades it.
        "query_prefix": "",
        # 568M params at 512 tokens still needs a smaller batch than bge-base on MPS.
        "batch_size": 8,
        "notes": "Multilingual (100+ languages), 568M params, ~2.2GB.",
    },
    "bge-small": {
        "hf_id": "BAAI/bge-small-en-v1.5",
        "dim": 384,
        "query_prefix": "Represent this sentence for searching relevant passages: ",
        "batch_size": 64,
        "notes": "English-only, 33M params, ~130MB. Speed reference point.",
    },
}

_MODEL_CACHE: dict[str, object] = {}


def get_model(model_key: str):
    """Load (and cache) a SentenceTransformer, preferring Apple MPS then CUDA."""
    if model_key in _MODEL_CACHE:
        return _MODEL_CACHE[model_key]

    from sentence_transformers import SentenceTransformer
    import torch

    if torch.cuda.is_available():
        device = "cuda"
    elif torch.backends.mps.is_available():
        device = "mps"
    else:
        device = "cpu"

    spec = EMBEDDING_MODELS[model_key]
    logger.info("[embed] loading %s on %s", spec["hf_id"], device)
    model = SentenceTransformer(spec["hf_id"], device=device)
    # Pin the context window so every model under comparison sees the same amount
    # of each chunk. See the MAX_SEQ_LENGTH comment above.
    model.max_seq_length = MAX_SEQ_LENGTH
    _MODEL_CACHE[model_key] = model
    return model


def collection_name(corpus: str, strategy: str, model_key: str) -> str:
    return f"exp_{corpus}_{strategy}_{model_key.replace('-', '')}"


def _sanitise(value):
    """Chroma metadata values must be str/int/float/bool — never None."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def count_truncated(texts: list[str], model_key: str) -> int:
    """How many chunks exceed the pinned context window and lose their tail."""
    model = get_model(model_key)
    tokenizer = model.tokenizer
    truncated = 0
    for text in texts:
        if len(tokenizer.encode(text, add_special_tokens=True)) > MAX_SEQ_LENGTH:
            truncated += 1
    return truncated


def embed_texts(texts: list[str], model_key: str, batch_size: int | None = None) -> tuple["object", float]:
    """Encode texts, returning (embeddings, wall_clock_seconds)."""
    import numpy as np  # noqa: F401  (returned array type)

    model = get_model(model_key)
    if batch_size is None:
        batch_size = EMBEDDING_MODELS[model_key].get("batch_size", 32)
    start = time.perf_counter()
    embeddings = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=True,
        convert_to_numpy=True,
        # Cosine similarity on unit vectors reduces to a dot product, and Chroma's
        # collections are created with hnsw:space=cosine.
        normalize_embeddings=True,
    )
    return embeddings, time.perf_counter() - start


def build_one(corpus: str, strategy: str, model_key: str, force: bool = False) -> dict:
    import chromadb
    from chromadb.config import Settings

    chunks = build_chunks(strategy)
    name = collection_name(corpus, strategy, model_key)

    client = chromadb.PersistentClient(
        path=str(config.CHROMA_PERSIST_PATH),
        settings=Settings(anonymized_telemetry=False),
    )

    existing = {c.name for c in client.list_collections()}
    if name in existing:
        collection = client.get_collection(name)
        if collection.count() == len(chunks) and not force:
            logger.info("[index] %s already built (%d) — skipping", name, collection.count())
            return {
                "collection": name,
                "corpus": corpus,
                "strategy": strategy,
                "model": model_key,
                "chunks": len(chunks),
                "embeddings": collection.count(),
                "skipped": True,
            }
        client.delete_collection(name)

    collection = client.create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )

    texts = [c["text"] for c in chunks]
    truncated = count_truncated(texts, model_key)
    embeddings, seconds = embed_texts(texts, model_key)

    metadatas = [
        {k: _sanitise(v) for k, v in chunk.items() if k not in ("text", "section_text")}
        | {"section_text": _sanitise(chunk.get("section_text", ""))[:8000]}
        for chunk in chunks
    ]

    # Chroma rejects very large single writes; add in batches.
    BATCH = 512
    for start in range(0, len(chunks), BATCH):
        stop = start + BATCH
        collection.add(
            ids=[f"{name}:{i}" for i in range(start, min(stop, len(chunks)))],
            documents=texts[start:stop],
            embeddings=embeddings[start:stop].tolist(),
            metadatas=metadatas[start:stop],
        )

    count = collection.count()
    spec = EMBEDDING_MODELS[model_key]
    stats = {
        "collection": name,
        "corpus": corpus,
        "strategy": strategy,
        "model": model_key,
        "model_hf_id": spec["hf_id"],
        "dim": spec["dim"],
        "chunks": len(chunks),
        "embeddings": count,
        "max_seq_length": MAX_SEQ_LENGTH,
        "batch_size": spec.get("batch_size", 32),
        "truncated_chunks": truncated,
        "truncated_pct": round(100.0 * truncated / len(chunks), 2) if chunks else 0.0,
        "embed_seconds": round(seconds, 2),
        "chunks_per_second": round(len(chunks) / seconds, 2) if seconds else None,
        "skipped": False,
    }
    logger.info(
        "[index] %s -> %d embeddings in %.1fs (%.1f chunks/s)",
        name, count, seconds, stats["chunks_per_second"] or 0.0,
    )
    if count != len(chunks):
        logger.error("[index] COUNT MISMATCH: %d chunks but %d embeddings", len(chunks), count)
    return stats


def verify() -> list[dict]:
    import chromadb
    from chromadb.config import Settings

    client = chromadb.PersistentClient(
        path=str(config.CHROMA_PERSIST_PATH),
        settings=Settings(anonymized_telemetry=False),
    )
    rows = []
    for collection in client.list_collections():
        handle = client.get_collection(collection.name)
        rows.append({"collection": collection.name, "count": handle.count()})
    return sorted(rows, key=lambda r: r["collection"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default="statutes")
    parser.add_argument("--strategy", choices=STRATEGIES)
    parser.add_argument("--model", choices=list(EMBEDDING_MODELS))
    parser.add_argument("--all", action="store_true", help="build every combination")
    parser.add_argument("--verify", action="store_true", help="print collection counts")
    parser.add_argument("--force", action="store_true", help="rebuild even if counts match")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.verify:
        rows = verify()
        print(f"\n{'collection':52} {'embeddings':>10}")
        print("-" * 64)
        for row in rows:
            print(f"{row['collection']:52} {row['count']:>10,}")
        print(f"\ntotal collections: {len(rows)}")
        return 0

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    all_stats: list[dict] = []
    if BUILD_STATS_PATH.exists():
        all_stats = json.loads(BUILD_STATS_PATH.read_text())

    if args.all:
        combos = [(s, m) for m in ("bge-base", "bge-m3") for s in STRATEGIES]
    else:
        if not (args.strategy and args.model):
            parser.error("provide --strategy and --model, or use --all")
        combos = [(args.strategy, args.model)]

    fresh = []
    for strategy, model_key in combos:
        stats = build_one(args.corpus, strategy, model_key, force=args.force)
        fresh.append(stats)
        all_stats = [s for s in all_stats if s.get("collection") != stats["collection"]]
        all_stats.append(stats)

    BUILD_STATS_PATH.write_text(json.dumps(all_stats, indent=2) + "\n", encoding="utf-8")
    logger.info("[index] build stats -> %s", BUILD_STATS_PATH)

    print()
    for stats in fresh:
        timing = "(cached)" if stats["skipped"] else f"{stats.get('embed_seconds', 0)}s"
        print(
            f"  {stats['collection']:50} chunks={stats['chunks']:5} "
            f"embeddings={stats['embeddings']:5} {timing}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
