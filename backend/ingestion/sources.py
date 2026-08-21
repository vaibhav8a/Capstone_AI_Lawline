"""
sources.py — Authoritative source registry for the statute corpus.

Every statute the system ingests is declared here with its official URL so that
the corpus is reproducible from a fresh clone and every retrieved passage can be
traced back to a government source.

Provenance policy
-----------------
* Only official Government of India repositories are used. `indiacode.nic.in` is
  the India Code portal maintained by the Legislative Department, Ministry of Law
  and Justice — it is the authoritative public source for Central Acts.
* Each document is fetched exactly once per run and cached on disk by SHA-256.
  There is no crawling, no link-following and no bulk harvesting.

IPC vs BNS — read this before adding a source
---------------------------------------------
The Indian Penal Code, 1860 was repealed and replaced by the Bharatiya Nyaya
Sanhita, 2023, which commenced on 1 July 2024. Both are carried in this corpus
but they are NEVER merged:

* IPC  → `legal_status="repealed"`,  applies to offences committed before 2024-07-01
* BNS  → `legal_status="in_force"`,  applies to offences committed on/after 2024-07-01

A repealed statute is still legally relevant (prosecutions begun under the IPC
continue under it), which is why it is retained rather than deleted. Retrieval
results must always surface `legal_status` so a reader is never shown a repealed
provision without knowing it is repealed.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Literal


@dataclass(frozen=True)
class StatuteSource:
    """A single statute document and everything needed to cite it."""

    # Stable identifier used as the corpus key and metadata `document` value.
    doc_id: str
    # Full official short title of the Act.
    title: str
    # e.g. "45 of 1860"
    act_number: str
    year: int
    # Direct link to the official PDF on the publishing government repository.
    url: str
    # Human-readable name of the publishing body, for the citation line.
    publisher: str
    # "repealed" statutes are retained for historical queries; see module docstring.
    legal_status: Literal["in_force", "repealed"]
    # Date the statute came into force (ISO). None where not applicable.
    commencement_date: str | None
    # For repealed Acts: the date the repeal took effect, and what replaced it.
    repealed_date: str | None = None
    replaced_by: str | None = None
    # The consolidation date of THIS document. Government PDFs are point-in-time
    # snapshots, so this is not the same as the Act's own currency — see
    # `version_caveat`. Carried onto every chunk so no answer can present a stale
    # provision as if it were the operative text.
    amended_up_to: str | None = None
    version_caveat: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ── Registry ────────────────────────────────────────────────────────────────
# Keep this list small and authoritative. Adding a non-government source requires
# a documented justification in docs/data_pipeline.md.

STATUTE_SOURCES: tuple[StatuteSource, ...] = (
    StatuteSource(
        doc_id="IPC",
        title="The Indian Penal Code, 1860",
        act_number="45 of 1860",
        year=1860,
        url="https://www.indiacode.nic.in/bitstream/123456789/4219/1/THE-INDIAN-PENAL-CODE-1860.pdf",
        publisher="India Code, Legislative Department, Ministry of Law and Justice, Government of India",
        legal_status="repealed",
        commencement_date="1862-01-01",
        repealed_date="2024-07-01",
        replaced_by="BNS",
        # Verified against the file itself: PDF creationDate is 2006-08-30 and the
        # newest amending Act referenced anywhere in the text is from 1997.
        amended_up_to="1997",
        version_caveat=(
            "This India Code consolidation reflects the IPC as amended up to 1997. "
            "It does NOT incorporate the Criminal Law (Amendment) Act, 2013 or the "
            "Criminal Law (Amendment) Act, 2018. Sections 375, 376 and 376A carry "
            "their pre-2013 text, and sections 354A-354D, 376AB, 376DA and 376DB "
            "are absent entirely. Treat these provisions as historical text, not as "
            "the law in force at the date of the IPC's repeal."
        ),
        notes=(
            "Repealed by s.358 of the Bharatiya Nyaya Sanhita, 2023 w.e.f. 2024-07-01. "
            "Retained in the corpus because offences committed before that date "
            "continue to be tried under the IPC."
        ),
    ),
    StatuteSource(
        doc_id="BNS",
        title="The Bharatiya Nyaya Sanhita, 2023",
        act_number="45 of 2023",
        year=2023,
        url="https://www.indiacode.nic.in/bitstream/123456789/20062/1/a202345.pdf",
        publisher="India Code, Legislative Department, Ministry of Law and Justice, Government of India",
        legal_status="in_force",
        commencement_date="2024-07-01",
        amended_up_to="2023",
        version_caveat=(
            "As enacted. Any amendment made after 25 December 2023 is not reflected; "
            "re-run the fetcher to pick up a newer consolidation if India Code publishes one."
        ),
        notes="Replaced the Indian Penal Code, 1860 with effect from 2024-07-01.",
    ),
)

# Provisions whose text in the cached IPC consolidation is known to be superseded.
# Retrieval must attach an explicit warning when any of these surface, because the
# stale text is materially different from the law as it stood at repeal — not a
# cosmetic difference.
IPC_SUPERSEDED_SECTIONS: dict[str, str] = {
    "375": "Substantially rewritten by the Criminal Law (Amendment) Act, 2013. The text here is the pre-2013 definition of rape.",
    "376": "Substantially rewritten by the Criminal Law (Amendment) Act, 2013, and further amended in 2018. The text here is the pre-2013 punishment provision.",
    "376A": "Replaced by the Criminal Law (Amendment) Act, 2013. The text here is the unrelated pre-2013 provision on intercourse during separation.",
    "354": "Supplemented by ss.354A-354D (sexual harassment, disrobing, voyeurism, stalking), inserted by the Criminal Law (Amendment) Act, 2013 and absent from this consolidation.",
    "228A": "Amended after 1997; verify against a current consolidation before relying on it.",
}

SOURCES_BY_ID = {s.doc_id: s for s in STATUTE_SOURCES}
