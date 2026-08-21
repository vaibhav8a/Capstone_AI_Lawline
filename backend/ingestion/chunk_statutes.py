"""
chunk_statutes.py — Step 5 of the ingestion pipeline: semantic chunking.

    … Metadata Extraction
            ↓
    >>> Semantic Chunking <<<   (this module)
            ↓
    Embedding Generation → Vector Index

Implements three chunking strategies so the choice can be *measured* rather than
asserted. All three consume the same parsed sections and emit the same record
shape, so a retrieval experiment can swap between them and change nothing else.

    fixed_window    Naive baseline. Splits the concatenated corpus into
                    fixed-size overlapping windows, ignoring section
                    boundaries entirely — the "blindly split at arbitrary
                    lengths" approach that legal text is not supposed to get.
                    Included as the control, not as a recommendation.

    section_whole   One chunk per statutory section. The section is the unit a
                    lawyer cites and the unit an offence is defined in, so this
                    is the legally natural boundary. Long sections stay whole.

    section_split   Section-aware with a length cap: sections longer than
                    MAX_CHUNK_WORDS are split at sentence boundaries, and every
                    resulting sub-chunk is prefixed with its own section heading
                    so the fragment remains self-identifying when retrieved
                    alone. Sub-chunks keep a pointer to the full section text so
                    the generator can be handed the whole provision.

Token budgets are measured in whitespace words rather than model tokens on
purpose: the chunk boundaries must be identical across the embedding models
being compared, otherwise the model comparison is confounded by a different
corpus. Roughly 1 word ≈ 1.3 BGE tokens.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

import config  # noqa: E402
from backend.ingestion.sources import STATUTE_SOURCES  # noqa: E402
from backend.ingestion.parse_statutes import PARSED_STATUTE_DIR  # noqa: E402

logger = logging.getLogger(__name__)

CHUNKED_STATUTE_DIR = config.BASE_DIR / "data" / "processed" / "chunks"

# Applies to the section_split strategy only.
MAX_CHUNK_WORDS = 220
SPLIT_OVERLAP_SENTENCES = 1

# Applies to the fixed_window baseline only.
WINDOW_WORDS = 220
WINDOW_OVERLAP_WORDS = 50

STRATEGIES = ("fixed_window", "section_whole", "section_split")

# Split on sentence enders, but not on the abbreviations that saturate statutes
# ("s. 420", "cl. (a)", "Act No. 45", "Rs. 500") — splitting there would cut a
# provision mid-citation.
_ABBREVIATIONS = r"(?<!\bs)(?<!\bcl)(?<!\bNo)(?<!\bRs)(?<!\bArt)(?<!\bsub)(?<!\bcf)"
SENTENCE_END_RE = re.compile(rf"{_ABBREVIATIONS}(?<=[.;:])\s+(?=[A-Z(\[])")


def _sentences(text: str) -> list[str]:
    parts = [p.strip() for p in SENTENCE_END_RE.split(text) if p.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _base_metadata(section: dict) -> dict:
    """Citation metadata copied onto every chunk derived from a section."""
    return {
        # `law` and `document_type` are the production-facing names for corpus
        # identity: retrieval filters on `law` so an IPC query is never answered
        # from BNS text or vice versa.
        #
        # `document` carries the same value and is kept deliberately: the
        # retrieval experiments in evaluation/ key on it, and renaming it would
        # invalidate the saved results in evaluation/results/. Both fields are
        # written from the same source, so they cannot drift.
        "law": section["document"],
        "document_type": "statute",
        "document": section["document"],
        "act_title": section["act_title"],
        "act_number": section["act_number"],
        "section": section["section"],
        "title": section["title"],
        "chapter": section["chapter"],
        "chapter_title": section["chapter_title"],
        "legal_status": section["legal_status"],
        "amended_up_to": section["amended_up_to"],
        "superseded_note": section["superseded_note"],
        "url": section["url"],
        "publisher": section["publisher"],
        # Short human-readable provenance label for the citation card in the UI.
        "source": "India Code",
        "source_type": "statute",
    }


def chunk_section_whole(sections: list[dict]) -> list[dict]:
    chunks = []
    for section in sections:
        text = section["text"].strip()
        if not text:
            continue
        meta = _base_metadata(section)
        chunks.append(
            {
                **meta,
                "chunk_id": f"{section['document']}-s{section['section']}-0",
                # The heading is prepended to the embedded text so that a query
                # naming the offence ("cheating") can match a section whose body
                # never repeats its own title.
                "text": f"{section['document']} Section {section['section']}. {section['title']}. {text}",
                "section_text": text,
                "chunk_index": 0,
                "chunk_count": 1,
                "strategy": "section_whole",
            }
        )
    return chunks


def chunk_section_split(sections: list[dict]) -> list[dict]:
    chunks = []
    for section in sections:
        text = section["text"].strip()
        if not text:
            continue
        meta = _base_metadata(section)
        heading = f"{section['document']} Section {section['section']}. {section['title']}."

        if len(text.split()) <= MAX_CHUNK_WORDS:
            pieces = [text]
        else:
            pieces = []
            current: list[str] = []
            current_len = 0
            for sentence in _sentences(text):
                sentence_len = len(sentence.split())
                if current and current_len + sentence_len > MAX_CHUNK_WORDS:
                    pieces.append(" ".join(current))
                    # Carry the tail sentence forward so a provision split across
                    # two chunks keeps its conditional clause attached.
                    current = current[-SPLIT_OVERLAP_SENTENCES:] if SPLIT_OVERLAP_SENTENCES else []
                    current_len = sum(len(s.split()) for s in current)
                current.append(sentence)
                current_len += sentence_len
            if current:
                pieces.append(" ".join(current))

        for index, piece in enumerate(pieces):
            chunks.append(
                {
                    **meta,
                    "chunk_id": f"{section['document']}-s{section['section']}-{index}",
                    "text": f"{heading} {piece}",
                    "section_text": text,
                    "chunk_index": index,
                    "chunk_count": len(pieces),
                    "strategy": "section_split",
                }
            )
    return chunks


def chunk_fixed_window(sections: list[dict]) -> list[dict]:
    """Baseline: ignore section boundaries and cut every N words.

    Each window is attributed to whichever section contributed its first word,
    which is what a boundary-unaware pipeline would report as the citation. That
    mis-attribution is precisely the failure this baseline exists to expose.
    """
    words: list[str] = []
    owners: list[dict] = []
    for section in sections:
        text = section["text"].strip()
        if not text:
            continue
        tokens = f"{section['document']} Section {section['section']}. {section['title']}. {text}".split()
        words.extend(tokens)
        owners.extend([section] * len(tokens))

    chunks = []
    step = max(WINDOW_WORDS - WINDOW_OVERLAP_WORDS, 1)
    for index, start in enumerate(range(0, len(words), step)):
        window = words[start : start + WINDOW_WORDS]
        if not window:
            break
        owner = owners[start]
        meta = _base_metadata(owner)
        chunks.append(
            {
                **meta,
                "chunk_id": f"win-{index}",
                "text": " ".join(window),
                "section_text": owner["text"],
                "chunk_index": index,
                "chunk_count": 0,
                "strategy": "fixed_window",
            }
        )
        if start + WINDOW_WORDS >= len(words):
            break
    return chunks


CHUNKERS = {
    "fixed_window": chunk_fixed_window,
    "section_whole": chunk_section_whole,
    "section_split": chunk_section_split,
}


def load_sections() -> list[dict]:
    sections: list[dict] = []
    for source in STATUTE_SOURCES:
        path = PARSED_STATUTE_DIR / f"{source.doc_id}_sections.json"
        if not path.exists():
            raise FileNotFoundError(
                f"{path} not found. Run: python -m backend.ingestion.parse_statutes"
            )
        sections.extend(json.loads(path.read_text(encoding="utf-8")))
    return sections


def build(strategy: str, sections: list[dict] | None = None) -> list[dict]:
    if strategy not in CHUNKERS:
        raise ValueError(f"unknown strategy {strategy!r}; choose from {STRATEGIES}")
    sections = sections if sections is not None else load_sections()
    chunks = CHUNKERS[strategy](sections)
    logger.info("[chunk] %-14s %d sections -> %d chunks", strategy, len(sections), len(chunks))
    return chunks


def build_all() -> dict[str, list[dict]]:
    CHUNKED_STATUTE_DIR.mkdir(parents=True, exist_ok=True)
    sections = load_sections()
    out = {}
    for strategy in STRATEGIES:
        chunks = build(strategy, sections)
        path = CHUNKED_STATUTE_DIR / f"statutes_{strategy}.json"
        path.write_text(json.dumps(chunks, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        out[strategy] = chunks
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=STRATEGIES, help="build one strategy only")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    built = {args.strategy: build(args.strategy)} if args.strategy else build_all()

    print()
    for strategy, chunks in built.items():
        lengths = [len(c["text"].split()) for c in chunks]
        lengths.sort()
        median = lengths[len(lengths) // 2] if lengths else 0
        print(
            f"  {strategy:14} chunks={len(chunks):5}  "
            f"words: min={min(lengths, default=0)} median={median} max={max(lengths, default=0)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
