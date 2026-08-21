"""
index_judgments.py — chunk and embed the Supreme Court judgment corpus.

Judgments go into their **own** ChromaDB collection, not the statute one:

  * they are a different `source_type` with a different metadata shape
  * mixing them would let a statute query return judgment prose as if it were
    the provision's text
  * keeping them separate means the statute retrieval experiments in
    evaluation/results/ remain valid after this corpus is added

Chunking differs from the statute pipeline for a structural reason. A statutory
section is a self-contained unit and is stored whole. A judgment is tens of
thousands of characters with no equivalent unit, so it is split into overlapping
passages, each carrying the full case citation so a retrieved passage is always
attributable.

Usage
-----
    python -m backend.ingestion.index_judgments
    python -m backend.ingestion.index_judgments --verify
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

import config  # noqa: E402
from backend.ingestion.build_index import (  # noqa: E402
    EMBEDDING_MODELS,
    _sanitise,
    embed_texts,
)

logger = logging.getLogger(__name__)

JUDGMENTS_PATH = config.BASE_DIR / "data" / "processed" / "judgments_sc" / "judgments.json"
STATS_PATH = config.BASE_DIR / "data" / "processed" / "judgments_sc" / "index_stats.json"

PASSAGE_WORDS = 220
PASSAGE_OVERLAP = 40

SENTENCE_END_RE = re.compile(r"(?<!\bs)(?<!\bNo)(?<!\bv)(?<=[.;])\s+(?=[A-Z(\[])")


def _passages(text: str) -> list[str]:
    """Split a judgment into overlapping, sentence-aligned passages."""
    sentences = [s.strip() for s in SENTENCE_END_RE.split(text) if s.strip()]
    if not sentences:
        return []

    passages: list[str] = []
    current: list[str] = []
    length = 0
    for sentence in sentences:
        words = len(sentence.split())
        if current and length + words > PASSAGE_WORDS:
            passages.append(" ".join(current))
            # Carry a tail of the previous passage so a holding split across a
            # boundary is still retrievable from either side.
            carry: list[str] = []
            carried = 0
            for previous in reversed(current):
                carried += len(previous.split())
                carry.insert(0, previous)
                if carried >= PASSAGE_OVERLAP:
                    break
            current, length = carry, carried
        current.append(sentence)
        length += words
    if current:
        passages.append(" ".join(current))
    return passages


def build_chunks(records: list[dict]) -> list[dict]:
    chunks: list[dict] = []
    for record in records:
        pieces = _passages(record["text"])
        # A compact case header on every passage: without it a retrieved
        # paragraph cannot be attributed to a case by the embedding alone.
        header = f"{record['case_name']} ({record['court']}, {record['judgment_date']})."
        for index, piece in enumerate(pieces):
            chunks.append(
                {
                    "source_type": "judgment",
                    "document_type": "judgment",
                    "law": record["law"],
                    "case_name": record["case_name"],
                    "court": record["court"],
                    "judgment_date": record["judgment_date"],
                    "citation": record["citation"],
                    "neutral_citation": record["neutral_citation"],
                    "judge": record["judge"],
                    "disposal_nature": record["disposal_nature"],
                    "sections_referred": ", ".join(record["sections_referred"][:25]),
                    "statutes_referred": ", ".join(record["statutes_referred"]),
                    "source": "eCourts via AWS Open Data (CC-BY-4.0)",
                    "url": record["source_url"],
                    "retrieval_date": record["retrieval_date"],
                    "year": record["year"],
                    "chunk_id": f"{record['neutral_citation'] or record['case_name'][:40]}-{index}",
                    "chunk_index": index,
                    "chunk_count": len(pieces),
                    "text": f"{header} {piece}",
                    "passage_text": piece,
                }
            )
    return chunks


def build(force: bool = False) -> dict:
    import chromadb
    from chromadb.config import Settings

    if not JUDGMENTS_PATH.exists():
        raise FileNotFoundError(
            f"{JUDGMENTS_PATH} not found. Run: python -m backend.ingestion.fetch_judgments"
        )

    records = json.loads(JUDGMENTS_PATH.read_text(encoding="utf-8"))
    chunks = build_chunks(records)
    logger.info("[judgments] %d judgments -> %d passages", len(records), len(chunks))

    model_key = config.STATUTE_EMBED_MODEL_KEY
    name = config.JUDGMENT_COLLECTION

    client = chromadb.PersistentClient(
        path=str(config.CHROMA_PERSIST_PATH),
        settings=Settings(anonymized_telemetry=False),
    )
    existing = {c.name for c in client.list_collections()}
    if name in existing:
        collection = client.get_collection(name)
        if collection.count() == len(chunks) and not force:
            logger.info("[judgments] %s already built (%d)", name, collection.count())
            return {"collection": name, "chunks": len(chunks), "embeddings": collection.count(), "skipped": True}
        client.delete_collection(name)

    collection = client.create_collection(name=name, metadata={"hnsw:space": "cosine"})

    texts = [c["text"] for c in chunks]
    embeddings, seconds = embed_texts(texts, model_key)

    if embeddings.shape[1] != config.EMBEDDING_DIM:
        client.delete_collection(name)
        raise RuntimeError(
            f"produced {embeddings.shape[1]}-dim vectors, expected {config.EMBEDDING_DIM}"
        )

    metadatas = [
        {k: _sanitise(v) for k, v in chunk.items() if k not in ("text", "passage_text")}
        | {"passage_text": _sanitise(chunk["passage_text"])[:8000]}
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

    from collections import Counter

    stats = {
        "collection": name,
        "judgments": len(records),
        "chunks": len(chunks),
        "embeddings": collection.count(),
        "embedding_model": EMBEDDING_MODELS[model_key]["hf_id"],
        "embedding_dim": config.EMBEDDING_DIM,
        "embed_seconds": round(seconds, 2),
        "by_law": dict(Counter(r["law"] for r in records)),
        "by_year": dict(sorted(Counter(r["year"] for r in records).items())),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "skipped": False,
    }
    STATS_PATH.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    logger.info("[judgments] %s -> %d embeddings (%.1fs)", name, collection.count(), seconds)
    return stats


def verify() -> dict:
    import chromadb
    from chromadb.config import Settings

    client = chromadb.PersistentClient(
        path=str(config.CHROMA_PERSIST_PATH),
        settings=Settings(anonymized_telemetry=False),
    )
    name = config.JUDGMENT_COLLECTION
    if name not in {c.name for c in client.list_collections()}:
        return {"ok": False, "error": f"{name} does not exist"}
    collection = client.get_collection(name)
    count = collection.count()
    if count == 0:
        return {"ok": False, "error": f"{name} is empty"}
    sample = collection.get(limit=1, include=["embeddings", "metadatas"])
    dim = len(sample["embeddings"][0])
    return {
        "ok": dim == config.EMBEDDING_DIM,
        "collection": name,
        "count": count,
        "stored_dim": dim,
        "expected_dim": config.EMBEDDING_DIM,
        "sample_metadata_keys": sorted(sample["metadatas"][0].keys()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    report = verify() if args.verify else build(force=args.force)
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
