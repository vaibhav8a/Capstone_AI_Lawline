"""
parse_statutes.py — Steps 2-4 of the ingestion pipeline.

    Document Acquisition
            ↓
    >>> Cleaning → Normalisation → Metadata Extraction <<<   (this module)
            ↓
    Semantic Chunking → Embedding → Vector Index

Turns a statute PDF into one structured record per section:

    {
      "document": "IPC",
      "section": "420",
      "title": "Cheating and dishonestly inducing delivery of property",
      "text": "Whoever cheats and thereby dishonestly induces …",
      "chapter": "CHAPTER XVII",
      "chapter_title": "OF OFFENCES AGAINST PROPERTY",
      "legal_status": "repealed",
      "url": "https://www.indiacode.nic.in/…",
      ...
    }

Parsing notes
-------------
The India Code PDFs are uniformly typeset (a single 10pt font throughout), so
footnotes cannot be separated from body text by font size. Two structural facts
are used instead:

1.  Every section body is introduced as ``<number>. <Title>.--`` at the start of
    a line, with the title repeated from the marginal note directly above it.
2.  Section numbers increase monotonically through the Act, whereas footnote
    markers restart at 1 on every page.

Requiring the section number to advance is what removes footnote lines such as
``1. Subs. by Act 27 of 1870, s. 2, for the original s. 40.`` and inline
cross-references, which a regex alone matches in their hundreds.
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
from backend.ingestion.sources import (  # noqa: E402
    STATUTE_SOURCES,
    SOURCES_BY_ID,
    IPC_SUPERSEDED_SECTIONS,
    StatuteSource,
)
from backend.ingestion.fetch_statutes import RAW_STATUTE_DIR  # noqa: E402

logger = logging.getLogger(__name__)

PARSED_STATUTE_DIR = config.BASE_DIR / "data" / "processed" / "statutes"

# ``<number>. <Title>.--`` anchored to the start of a line. The trailing dash may
# be rendered as "-", "--", en-dash or em-dash depending on the source typesetting.
#
# Sections inserted by a later amendment are wrapped in an amendment marker, e.g.
#   1*[34. Acts done by several persons in furtherance of common intention.--…
# so the optional ``<digits>*[`` prefix must be tolerated. Without it s.34, s.124A
# and s.304A — all amendment insertions — are silently dropped.
#
# The title is matched with DOTALL because the typesetter wraps it across lines:
#   304A. Causing death\nby\nnegligence.--
SECTION_BODY_RE = re.compile(
    r"^(?:\d{1,2}\*+\s*\[\s*)?"
    r"(?P<num>\d{1,3}[A-Z]{0,2})\.[ \t]+(?P<title>.{3,250}?)\.[ \t]*[-–—]{1,2}",
    re.M | re.S,
)

# A bare marginal note: the section number alone on its own line.
MARGINAL_NOTE_RE = re.compile(r"^(?P<num>\d{1,3}[A-Z]{0,2})\.[ \t]*$", re.M)

CHAPTER_RE = re.compile(r"^CHAPTER\s+([IVXLC]+[A-Z]?)\s*$", re.M)

# Openers of amendment footnotes, used only as a secondary guard.
FOOTNOTE_OPENERS = re.compile(
    r"^(?:Subs\.?|Ins\.?|Rep\.?|Added|Omitted|Certain|The\s+words|The\s+brackets|"
    r"Clause|Chapter\s+[IVXLC]+[A-Z]?\s+(?:ins|inserted|added)|S\.\s*\d)",
    re.I,
)

# Sections repealed in place read e.g. "[Definition of "British India".] Rep. by …"
REPEALED_IN_PLACE_RE = re.compile(r"^\s*\[?.*?\]?\s*Rep(?:ealed)?\.?\s+by\b", re.I)


def _section_sort_key(number: str) -> tuple[int, str]:
    """Order 304 < 304A < 304B < 305."""
    match = re.match(r"^(\d+)([A-Z]*)$", number)
    if not match:
        return (10**6, number)
    return (int(match.group(1)), match.group(2))


def extract_text(pdf_path: Path) -> str:
    """Extract text and normalise the typesetting artefacts we depend on."""
    import fitz  # PyMuPDF

    doc = fitz.open(pdf_path)
    try:
        raw = "\n".join(doc[page].get_text() for page in range(doc.page_count))
    finally:
        doc.close()

    # U+00AD SOFT HYPHEN is used mid-word and inside the ".--" separator.
    raw = raw.replace("­", "")
    # Normalise non-breaking spaces before whitespace collapsing.
    raw = raw.replace(" ", " ")
    # The layout is mostly blank lines; drop them and trim each surviving line.
    lines = [line.strip() for line in raw.split("\n")]
    return "\n".join(line for line in lines if line)


def _chapter_index(text: str) -> list[tuple[int, str, str]]:
    """Return (offset, chapter_number, chapter_title) for each chapter heading."""
    chapters: list[tuple[int, str, str]] = []
    for match in CHAPTER_RE.finditer(text):
        # The chapter title is the next non-empty line after the heading.
        tail = text[match.end() : match.end() + 200].lstrip("\n")
        title = tail.split("\n", 1)[0].strip() if tail else ""
        chapters.append((match.start(), match.group(1), title))
    return chapters


def _chapter_for(offset: int, chapters: list[tuple[int, str, str]]) -> tuple[str, str]:
    current = ("", "")
    for start, number, title in chapters:
        if start <= offset:
            current = (f"CHAPTER {number}", title)
        else:
            break
    return current


def parse_sections(text: str, source: StatuteSource) -> list[dict]:
    """Extract one record per section, in document order."""
    chapters = _chapter_index(text)
    candidates = list(SECTION_BODY_RE.finditer(text))

    accepted: list[re.Match] = []
    highest = (0, "")
    for match in candidates:
        number = match.group("num")
        title = " ".join(match.group("title").split())

        # Monotonicity: a real section always advances the numbering. Footnote
        # markers restart at 1 each page and inline cross-references point
        # backwards, so both fail this test.
        key = _section_sort_key(number)
        if key <= highest:
            continue
        # Secondary guard: an amendment footnote that happens to advance the
        # numbering still reads like one.
        if FOOTNOTE_OPENERS.match(title):
            continue

        accepted.append(match)
        highest = key

    records: list[dict] = []
    for index, match in enumerate(accepted):
        start = match.end()
        end = accepted[index + 1].start() if index + 1 < len(accepted) else len(text)
        body = text[start:end].strip()

        # The next section's marginal note ("421.\nDishonest or fraudulent …")
        # sits at the tail of this section's span. Remove it.
        body = _strip_trailing_marginal_note(body)

        number = match.group("num")
        title = " ".join(match.group("title").split())
        chapter, chapter_title = _chapter_for(match.start(), chapters)

        records.append(
            {
                "document": source.doc_id,
                "act_title": source.title,
                "act_number": source.act_number,
                "section": number,
                "title": title,
                "text": " ".join(body.split()),
                "chapter": chapter,
                "chapter_title": chapter_title,
                "legal_status": source.legal_status,
                "repealed_date": source.repealed_date,
                "replaced_by": source.replaced_by,
                "amended_up_to": source.amended_up_to,
                "url": source.url,
                "publisher": source.publisher,
                "section_repealed_in_place": bool(REPEALED_IN_PLACE_RE.match(body)),
                # Section-level currency warning, surfaced with the citation so a
                # reader is never shown superseded text without being told.
                "superseded_note": (
                    IPC_SUPERSEDED_SECTIONS.get(number, "")
                    if source.doc_id == "IPC"
                    else ""
                ),
            }
        )

    return records


def _strip_trailing_marginal_note(body: str) -> str:
    """Remove the next section's heading material from the tail of this section.

    A section's span runs up to the next section body, so it picks up two kinds of
    trailing heading that belong to the *following* section:

      * its marginal note  — "421.\\nDishonest or fraudulent removal …"
      * a cross-heading    — "Of fraudulent deeds and dispositions of property"

    Both would otherwise be embedded with the wrong section and pollute retrieval.
    """
    lines = body.split("\n")

    # Walk back over the trailing marginal note (number line + wrapped title).
    cut = len(lines)
    for index in range(len(lines) - 1, max(len(lines) - 6, 0) - 1, -1):
        if MARGINAL_NOTE_RE.match(lines[index] + "\n"):
            cut = index
            break
    lines = lines[:cut]

    # Then drop a trailing cross-heading: a short line with no terminal full stop,
    # typically "Of <topic>" or an all-caps rubric.
    while lines:
        tail = lines[-1].strip()
        is_cross_heading = (
            0 < len(tail) <= 90
            and not tail.endswith((".", ";", ":", ","))
            and (tail.startswith("Of ") or tail.isupper())
        )
        if not is_cross_heading:
            break
        lines.pop()

    return "\n".join(lines).strip()


def parse_one(source: StatuteSource) -> list[dict]:
    pdf_path = RAW_STATUTE_DIR / f"{source.doc_id}.pdf"
    if not pdf_path.exists():
        raise FileNotFoundError(
            f"{pdf_path} not found. Run: python -m backend.ingestion.fetch_statutes"
        )
    text = extract_text(pdf_path)
    records = parse_sections(text, source)
    logger.info(
        "[parse] %s: %d sections from %d chars", source.doc_id, len(records), len(text)
    )
    return records


def parse_all() -> dict[str, list[dict]]:
    PARSED_STATUTE_DIR.mkdir(parents=True, exist_ok=True)
    out: dict[str, list[dict]] = {}
    for source in STATUTE_SOURCES:
        records = parse_one(source)
        out[source.doc_id] = records
        destination = PARSED_STATUTE_DIR / f"{source.doc_id}_sections.json"
        destination.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        logger.info("[parse] wrote %s", destination)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc", help="parse a single document id (IPC, BNS)")
    parser.add_argument("--show", help="print one section by number, e.g. --show 420")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.doc:
        records = parse_one(SOURCES_BY_ID[args.doc])
        parsed = {args.doc: records}
    else:
        parsed = parse_all()

    for doc_id, records in parsed.items():
        print(f"\n{doc_id}: {len(records)} sections")
        if records:
            numbers = [r["section"] for r in records]
            print(f"  range: {numbers[0]} … {numbers[-1]}")
            print(f"  repealed in place: {sum(r['section_repealed_in_place'] for r in records)}")

        if args.show:
            for record in records:
                if record["section"] == args.show:
                    print(f"\n  [{doc_id} s.{record['section']}] {record['title']}")
                    print(f"  chapter: {record['chapter']} — {record['chapter_title']}")
                    print(f"  status : {record['legal_status']}")
                    print(f"  text   : {record['text'][:600]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
