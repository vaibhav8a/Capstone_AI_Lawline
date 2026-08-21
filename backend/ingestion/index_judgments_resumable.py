"""
index_judgments_resumable.py — checkpointed judgment indexing.

Why this replaces the one-shot builder
--------------------------------------
The original builder embedded all 17,342 passages in a single call and only then
wrote to ChromaDB. Interrupting it — because the Mac got hot, or the terminal
closed — discarded roughly 90 minutes of MPS work with nothing persisted. There
was no way to stop it safely and no way to resume.

This version processes the corpus in slices:

    for each slice of N passages:
        embed the slice
        write the slice to ChromaDB
        record the slice as done in a checkpoint file
        check for a stop request

ChromaDB is a persistent store, so a slice written is a slice kept. On resume,
already-written slices are skipped and embedding continues from the first
incomplete one. Interruption costs at most one slice (~30 seconds), not the run.

Duplicate protection
--------------------
Passage IDs are deterministic (`<collection>:<global-index>`), so re-writing a
slice upserts over itself rather than appending. Even a resume that repeats a
partially-written slice cannot inflate the collection.

Incremental growth
------------------
The corpus grows by appending judgments, so `build_chunks` reproduces the
existing passages in the same order at the same indices and the new ones follow
after. That makes already-embedded work reusable — but only if the indexer can
prove the prefix really is unchanged.

The first version could not. It accepted a checkpoint only when
`checkpoint["total"] == len(chunks)`, so adding a single judgment changed the
total, invalidated the checkpoint and re-embedded all 17,342 existing passages —
around 72 minutes of MPS work thrown away for no reason.

Now the store itself is the authority. On startup the indexer reads the IDs
already in ChromaDB, treats a slice as done only when every one of its IDs is
present, and spot-checks the stored documents against the freshly built chunks
byte-for-byte. A slice that matches is skipped; anything else is embedded. Two
consequences worth stating:

  * the partially-filled tail slice from the previous run is re-embedded, because
    it is no longer full. That is one slice, not a corpus.
  * if the prefix has genuinely changed — a judgment edited or reordered — the
    mismatch is detected and reported instead of silently corrupting the index.

Stopping
--------
Two mechanisms, both graceful:
  * SIGTERM/SIGINT — the handler finishes the current slice, then exits cleanly
  * a stop-request file — checked between slices, used by index-stop.sh

Either way the checkpoint is consistent and `--resume` continues from it.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

import config  # noqa: E402
from backend.ingestion.build_index import EMBEDDING_MODELS, _sanitise, embed_texts  # noqa: E402
from backend.ingestion.index_judgments import (  # noqa: E402
    JUDGMENTS_PATH,
    build_chunks,
)

logger = logging.getLogger(__name__)

RUN_DIR = config.BASE_DIR / ".run"
CHECKPOINT_PATH = RUN_DIR / "index_checkpoint.json"
PROGRESS_PATH = RUN_DIR / "index_progress.json"
STOP_REQUEST_PATH = RUN_DIR / "index.stop"

SLICE_SIZE = 256  # passages embedded+persisted per checkpoint

_stop_requested = False


def _handle_signal(signum, _frame):
    global _stop_requested
    _stop_requested = True
    logger.info("\n[index] signal %s received — finishing current slice, then stopping", signum)


def _should_stop() -> bool:
    return _stop_requested or STOP_REQUEST_PATH.exists()


def _load_checkpoint(total: int, collection_name: str) -> dict:
    """The checkpoint file is a hint. `_completed_slices` is the authority."""
    if CHECKPOINT_PATH.exists():
        data = json.loads(CHECKPOINT_PATH.read_text())
        if data.get("collection") == collection_name:
            data["total"] = total
            return data
        logger.warning("[index] checkpoint belongs to %s, not %s — starting fresh",
                       data.get("collection"), collection_name)
    return {
        "collection": collection_name,
        "total": total,
        "completed_slices": [],
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def _slice_bounds(total: int) -> list[tuple[int, int]]:
    return [(start, min(start + SLICE_SIZE, total)) for start in range(0, total, SLICE_SIZE)]


def _completed_slices(collection, name: str, texts: list[str], total: int,
                      strict: bool = False) -> tuple[set[int], list[str]]:
    """
    Which slices are already correctly embedded, according to ChromaDB.

    A slice counts as done when every ID in it exists in the collection AND the
    stored documents match what `build_chunks` produces now. Verifying against
    the store rather than a self-reported counter is what makes appending to the
    corpus safe: it proves the prefix is unchanged instead of assuming it.
    """
    if collection.count() == 0:
        return set(), []

    stored_ids: set[str] = set()
    offset, page = 0, 10000
    while True:
        batch = collection.get(limit=page, offset=offset, include=[])
        ids = batch.get("ids") or []
        if not ids:
            break
        stored_ids.update(ids)
        offset += len(ids)
        if len(ids) < page:
            break

    done: set[int] = set()
    to_check: list[str] = []
    for start, stop in _slice_bounds(total):
        # Bounds are computed against the CURRENT total, so a slice that was the
        # partial tail of an earlier run now extends over the newly appended
        # passages. Those IDs are absent from the store, the slice fails this
        # test, and it is re-embedded — one slice of rework, not a corpus.
        if all(f"{name}:{i}" in stored_ids for i in range(start, stop)):
            done.add(start)
            probes = range(start, stop) if strict else (start, (start + stop) // 2, stop - 1)
            to_check.extend(f"{name}:{i}" for i in probes)

    mismatches: list[str] = []
    for chunk_start in range(0, len(to_check), 2000):
        wanted = to_check[chunk_start:chunk_start + 2000]
        got = collection.get(ids=wanted, include=["documents"])
        found = dict(zip(got["ids"], got["documents"]))
        for passage_id in wanted:
            index = int(passage_id.rsplit(":", 1)[1])
            if found.get(passage_id) != texts[index]:
                mismatches.append(passage_id)

    if mismatches:
        # Drop every slice containing a mismatch; those get re-embedded.
        bad = {(int(m.rsplit(":", 1)[1]) // SLICE_SIZE) * SLICE_SIZE for m in mismatches}
        done -= bad
        logger.warning("[index] %d stored passages differ from the rebuilt corpus "
                       "— re-embedding %d affected slice(s)", len(mismatches), len(bad))
    return done, mismatches


def _save_checkpoint(data: dict) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CHECKPOINT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(CHECKPOINT_PATH)  # atomic: never a half-written checkpoint


def _write_progress(done: int, total: int, state: str, rate: float | None, eta: float | None) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    PROGRESS_PATH.write_text(json.dumps({
        "done": done,
        "total": total,
        "percent": round(100.0 * done / total, 2) if total else 0.0,
        "state": state,
        "model": EMBEDDING_MODELS[config.STATUTE_EMBED_MODEL_KEY]["hf_id"],
        "device": _device(),
        "passages_per_second": round(rate, 2) if rate else None,
        "eta_seconds": int(eta) if eta else None,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "pid": os.getpid(),
    }, indent=2))


def _device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _client():
    import chromadb
    from chromadb.config import Settings

    return chromadb.PersistentClient(
        path=str(config.CHROMA_PERSIST_PATH),
        settings=Settings(anonymized_telemetry=False),
    )


def run(reset: bool = False, strict: bool = False, plan_only: bool = False) -> dict:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    if not plan_only:
        STOP_REQUEST_PATH.unlink(missing_ok=True)

    if not JUDGMENTS_PATH.exists():
        raise FileNotFoundError(
            f"{JUDGMENTS_PATH} not found. Run: python -m backend.ingestion.fetch_judgments"
        )

    records = json.loads(JUDGMENTS_PATH.read_text(encoding="utf-8"))
    chunks = build_chunks(records)
    total = len(chunks)
    name = config.JUDGMENT_COLLECTION
    texts = [c["text"] for c in chunks]

    client = _client()
    existing = {c.name for c in client.list_collections()}

    if reset and name in existing:
        if plan_only:
            raise SystemExit("--plan and --reset are contradictory")
        client.delete_collection(name)
        existing.discard(name)
        CHECKPOINT_PATH.unlink(missing_ok=True)
        logger.info("[index] reset: dropped %s and its checkpoint", name)

    if name not in existing:
        if plan_only:
            collection = None
        else:
            collection = client.create_collection(name=name, metadata={"hnsw:space": "cosine"})
    else:
        collection = client.get_collection(name)

    checkpoint = _load_checkpoint(total, name)
    bounds = _slice_bounds(total)

    if collection is None:
        done_slices, mismatches = set(), []
    else:
        done_slices, mismatches = _completed_slices(collection, name, texts, total, strict=strict)
    checkpoint["completed_slices"] = sorted(done_slices)
    checkpoint["verified_against_store_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    remaining = [start for start, _ in bounds if start not in done_slices]
    done_count = sum(stop - start for start, stop in bounds if start in done_slices)
    reused = done_count

    logger.info(
        "[index] %d judgments -> %d passages | %d/%d slices verified in the store "
        "(%d passages reused, %d to embed)",
        len(records), total, len(done_slices), len(bounds),
        reused, total - reused,
    )

    if plan_only:
        first_new = min(remaining) if remaining else None
        rate = 4.0  # measured on this machine, BGE-M3 on MPS
        return {
            "collection": name,
            "judgments": len(records),
            "passages_total": total,
            "passages_already_embedded": reused,
            "passages_to_embed": total - reused,
            "first_passage_id_to_embed": f"{name}:{first_new}" if first_new is not None else None,
            "slices_to_embed": len(remaining),
            "prefix_mismatches": mismatches[:20],
            "assumed_passages_per_second": rate,
            "estimated_seconds": round((total - reused) / rate),
            "full_rebuild_seconds_avoided": round(reused / rate),
        }

    if not remaining:
        logger.info("[index] nothing to do — already complete")
        _write_progress(total, total, "complete", None, None)
        return _summary(records, collection, total, complete=True)

    metadatas = [
        {k: _sanitise(v) for k, v in chunk.items() if k not in ("text", "passage_text")}
        | {"passage_text": _sanitise(chunk["passage_text"])[:8000]}
        for chunk in chunks
    ]

    started = time.perf_counter()
    processed_this_run = 0

    for start in remaining:
        if _should_stop():
            logger.info("[index] stop requested — %d/%d passages persisted", done_count, total)
            _write_progress(done_count, total, "stopped", None, None)
            _save_checkpoint(checkpoint)
            return _summary(records, collection, total, complete=False)

        stop = min(start + SLICE_SIZE, total)
        _write_progress(done_count, total, "running", None, None)

        embeddings, _ = embed_texts(texts[start:stop], config.STATUTE_EMBED_MODEL_KEY)

        # Deterministic IDs: re-running a slice upserts rather than duplicates.
        collection.upsert(
            ids=[f"{name}:{i}" for i in range(start, stop)],
            documents=texts[start:stop],
            embeddings=embeddings.tolist(),
            metadatas=metadatas[start:stop],
        )

        checkpoint["completed_slices"] = sorted(set(checkpoint["completed_slices"]) | {start})
        checkpoint["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        _save_checkpoint(checkpoint)

        processed_this_run += (stop - start)
        done_count += (stop - start)

        elapsed = time.perf_counter() - started
        rate = processed_this_run / elapsed if elapsed > 0 else None
        eta = (total - done_count) / rate if rate else None
        _write_progress(done_count, total, "running", rate, eta)

        logger.info(
            "[index] %6d/%d (%5.1f%%)  %.1f passages/s  eta %s",
            done_count, total, 100.0 * done_count / total,
            rate or 0.0,
            time.strftime("%H:%M:%S", time.gmtime(eta)) if eta else "?",
        )

    _write_progress(total, total, "complete", None, None)
    logger.info("[index] complete: %d embeddings in %s", collection.count(), name)
    return _summary(records, collection, total, complete=True)


def _summary(records, collection, total, complete: bool) -> dict:
    from collections import Counter

    summary = {
        "collection": config.JUDGMENT_COLLECTION,
        "judgments": len(records),
        "passages_expected": total,
        "embeddings": collection.count(),
        "complete": complete and collection.count() == total,
        "embedding_model": EMBEDDING_MODELS[config.STATUTE_EMBED_MODEL_KEY]["hf_id"],
        "embedding_dim": config.EMBEDDING_DIM,
        "device": _device(),
        "by_law": dict(Counter(r["law"] for r in records)),
    }
    if summary["complete"]:
        stats_path = config.BASE_DIR / "data" / "processed" / "judgments_sc" / "index_stats.json"
        stats_path.parent.mkdir(parents=True, exist_ok=True)
        stats_path.write_text(json.dumps(summary | {
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")
        }, indent=2) + "\n", encoding="utf-8")
    return summary


def status() -> dict:
    if not PROGRESS_PATH.exists():
        return {"state": "not_started"}
    progress = json.loads(PROGRESS_PATH.read_text())
    # The recorded state can be stale if the process died; reconcile with reality.
    pid = progress.get("pid")
    alive = False
    if pid:
        try:
            os.kill(pid, 0)
            alive = True
        except (OSError, ProcessLookupError):
            alive = False
    if progress.get("state") == "running" and not alive:
        progress["state"] = "interrupted"
    return progress


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true", help="continue from the checkpoint")
    parser.add_argument("--reset", action="store_true", help="discard progress and rebuild")
    parser.add_argument("--status", action="store_true", help="print progress and exit")
    parser.add_argument("--plan", action="store_true",
                        help="report what would be embedded, embed nothing")
    parser.add_argument("--strict", action="store_true",
                        help="compare every stored passage, not a sample per slice")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.status:
        print(json.dumps(status(), indent=2))
        return 0

    report = run(reset=args.reset, strict=args.strict, plan_only=args.plan)
    print(json.dumps(report, indent=2))
    if args.plan:
        return 0
    return 0 if report.get("complete") else 2


if __name__ == "__main__":
    raise SystemExit(main())
