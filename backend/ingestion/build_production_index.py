"""
build_production_index.py — build the collection the live application serves.

Deliberately separate from `build_index.py`, which builds the `exp_*` collections
used by the retrieval experiments. Those are research artifacts and this script
never touches them.

The production collection is named after its embedding model
(`prod_statutes_section_whole_bgem3`) because a Chroma collection is only valid
for the dimensionality it was built with. This project began with exactly that
bug: `config.py` declared bge-m3 (1024d) while the index on disk was 768d, so
every dense query silently returned nothing and the system degraded to BM25
without raising an error.

To make that class of failure loud rather than silent, the builder refuses to
write into a collection whose stored dimensionality differs from the configured
model, and `verify()` checks the dimensionality of what it actually finds.

Usage
-----
    python -m backend.ingestion.build_production_index
    python -m backend.ingestion.build_production_index --force
    python -m backend.ingestion.build_production_index --verify
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
from backend.ingestion.build_index import (  # noqa: E402
    EMBEDDING_MODELS,
    MAX_SEQ_LENGTH,
    _sanitise,
    count_truncated,
    embed_texts,
)
from backend.ingestion.chunk_statutes import build as build_chunks  # noqa: E402

logger = logging.getLogger(__name__)

PROD_STATS_PATH = config.BASE_DIR / "data" / "processed" / "production_index.json"


def _client():
    import chromadb
    from chromadb.config import Settings

    return chromadb.PersistentClient(
        path=str(config.CHROMA_PERSIST_PATH),
        settings=Settings(anonymized_telemetry=False),
    )


def build(force: bool = False) -> dict:
    model_key = config.STATUTE_EMBED_MODEL_KEY
    strategy = config.STATUTE_CHUNK_STRATEGY
    name = config.STATUTE_COLLECTION
    spec = EMBEDDING_MODELS[model_key]

    if spec["dim"] != config.EMBEDDING_DIM:
        raise RuntimeError(
            f"config.EMBEDDING_DIM is {config.EMBEDDING_DIM} but model "
            f"{model_key} produces {spec['dim']}-dim vectors. Fix config.py "
            "before building — a mismatch here is what silently broke retrieval."
        )

    chunks = build_chunks(strategy)
    client = _client()

    existing = {c.name for c in client.list_collections()}
    if name in existing:
        collection = client.get_collection(name)
        if collection.count() == len(chunks) and not force:
            logger.info("[prod] %s already built (%d chunks)", name, collection.count())
            return _stats(name, strategy, model_key, chunks, collection.count(), None, 0, True)
        logger.info("[prod] rebuilding %s", name)
        client.delete_collection(name)

    collection = client.create_collection(name=name, metadata={"hnsw:space": "cosine"})

    texts = [c["text"] for c in chunks]
    truncated = count_truncated(texts, model_key)
    embeddings, seconds = embed_texts(texts, model_key)

    if embeddings.shape[1] != config.EMBEDDING_DIM:
        client.delete_collection(name)
        raise RuntimeError(
            f"produced {embeddings.shape[1]}-dim vectors, expected "
            f"{config.EMBEDDING_DIM}. Refusing to persist a mismatched index."
        )

    metadatas = [
        {k: _sanitise(v) for k, v in chunk.items() if k not in ("text", "section_text")}
        | {"section_text": _sanitise(chunk.get("section_text", ""))[:8000]}
        for chunk in chunks
    ]

    BATCH = 512
    for start in range(0, len(chunks), BATCH):
        stop = min(start + BATCH, len(chunks))
        collection.add(
            ids=[f"{name}:{i}" for i in range(start, stop)],
            documents=texts[start:stop],
            embeddings=embeddings[start:stop].tolist(),
            metadatas=metadatas[start:stop],
        )

    count = collection.count()
    if count != len(chunks):
        raise RuntimeError(f"wrote {count} embeddings for {len(chunks)} chunks")

    stats = _stats(name, strategy, model_key, chunks, count, seconds, truncated, False)
    PROD_STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROD_STATS_PATH.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    logger.info("[prod] %s -> %d embeddings (%.1fs)", name, count, seconds or 0.0)
    return stats


def _stats(name, strategy, model_key, chunks, count, seconds, truncated, cached) -> dict:
    from collections import Counter

    by_law = Counter(c["law"] for c in chunks)
    return {
        "collection": name,
        "chunk_strategy": strategy,
        "embedding_model": model_key,
        "embedding_hf_id": EMBEDDING_MODELS[model_key]["hf_id"],
        "embedding_dim": EMBEDDING_MODELS[model_key]["dim"],
        "max_seq_length": MAX_SEQ_LENGTH,
        "chunks": len(chunks),
        "embeddings": count,
        "by_law": dict(by_law),
        "truncated_chunks": truncated,
        "embed_seconds": round(seconds, 2) if seconds else None,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "reused_cache": cached,
    }


def verify() -> dict:
    """Confirm the live collection exists, is populated, and has the right dim."""
    name = config.STATUTE_COLLECTION
    client = _client()
    names = {c.name for c in client.list_collections()}
    if name not in names:
        return {"ok": False, "error": f"collection {name!r} does not exist"}

    collection = client.get_collection(name)
    count = collection.count()
    if count == 0:
        return {"ok": False, "error": f"collection {name!r} is empty"}

    sample = collection.get(limit=1, include=["embeddings", "metadatas"])
    dim = len(sample["embeddings"][0])
    meta = sample["metadatas"][0]

    from collections import Counter

    everything = collection.get(include=["metadatas"])
    by_law = Counter(m.get("law", "?") for m in everything["metadatas"])

    ok = dim == config.EMBEDDING_DIM
    return {
        "ok": ok,
        "collection": name,
        "count": count,
        "stored_dim": dim,
        "expected_dim": config.EMBEDDING_DIM,
        "by_law": dict(by_law),
        "sample_metadata_keys": sorted(meta.keys()),
        "error": None if ok else (
            f"dimension mismatch: stored {dim}, config expects {config.EMBEDDING_DIM}"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.verify:
        report = verify()
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1

    stats = build(force=args.force)
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
