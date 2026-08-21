"""Tests for statute chunking and section parsing.

Focus is on the properties that retrieval quality depends on: a chunk must keep
its own citation metadata, must not absorb the next section's heading, and must
be attributable back to a real section.
"""

import pytest

from backend.ingestion.chunk_statutes import (
    MAX_CHUNK_WORDS,
    build,
    chunk_fixed_window,
    chunk_section_split,
    chunk_section_whole,
)
from backend.ingestion.parse_statutes import (
    _strip_trailing_marginal_note,
    _section_sort_key,
    SECTION_BODY_RE,
)

CITATION_FIELDS = ("document", "section", "title", "url", "legal_status", "chunk_id")


def make_section(section="420", text="Whoever cheats shall be punished.", **overrides):
    record = {
        "document": "IPC",
        "act_title": "The Indian Penal Code, 1860",
        "act_number": "45 of 1860",
        "section": section,
        "title": "Cheating and dishonestly inducing delivery of property",
        "text": text,
        "chapter": "CHAPTER XVII",
        "chapter_title": "OF OFFENCES AGAINST PROPERTY",
        "legal_status": "repealed",
        "repealed_date": "2024-07-01",
        "replaced_by": "BNS",
        "amended_up_to": "1997",
        "url": "https://www.indiacode.nic.in/example.pdf",
        "publisher": "India Code",
        "section_repealed_in_place": False,
        "superseded_note": "",
    }
    record.update(overrides)
    return record


class TestSectionSortKey:
    def test_letter_suffix_orders_after_plain_number(self):
        assert _section_sort_key("304") < _section_sort_key("304A")
        assert _section_sort_key("304A") < _section_sort_key("304B")
        assert _section_sort_key("304B") < _section_sort_key("305")

    def test_numeric_not_lexicographic(self):
        # "9" must sort before "10"; a string comparison would get this wrong.
        assert _section_sort_key("9") < _section_sort_key("10")


class TestSectionBodyRegex:
    def test_matches_plain_section(self):
        match = SECTION_BODY_RE.search("420. Cheating and dishonesty.--Whoever cheats")
        assert match and match.group("num") == "420"

    def test_matches_amendment_inserted_section(self):
        # s.34, s.124A and s.304A are all wrapped in an amendment marker in the
        # official PDF; without this the corpus silently loses them.
        match = SECTION_BODY_RE.search("1*[34. Acts done by several persons.--When a criminal act")
        assert match and match.group("num") == "34"

    def test_matches_title_wrapped_across_lines(self):
        match = SECTION_BODY_RE.search("304A. Causing death\nby\nnegligence.--Whoever causes")
        assert match and match.group("num") == "304A"

    def test_preserves_letter_suffix(self):
        match = SECTION_BODY_RE.search("124A. Sedition.--Whoever by words")
        assert match and match.group("num") == "124A"


class TestStripTrailingMarginalNote:
    def test_removes_next_section_marginal_note(self):
        body = "Whoever cheats shall be punished.\n421.\nDishonest removal of property."
        assert "421" not in _strip_trailing_marginal_note(body)

    def test_removes_trailing_cross_heading(self):
        body = "Whoever cheats shall be punished.\nOf fraudulent deeds and dispositions of property"
        assert _strip_trailing_marginal_note(body) == "Whoever cheats shall be punished."

    def test_keeps_ordinary_body_text(self):
        body = "Whoever cheats shall be punished with imprisonment."
        assert _strip_trailing_marginal_note(body) == body


class TestSectionWhole:
    def test_one_chunk_per_section(self):
        chunks = chunk_section_whole([make_section("420"), make_section("421")])
        assert len(chunks) == 2

    def test_carries_citation_metadata(self):
        chunk = chunk_section_whole([make_section()])[0]
        for field in CITATION_FIELDS:
            assert chunk.get(field), f"missing citation field {field!r}"

    def test_embedded_text_includes_heading(self):
        # The body of s.420 never repeats the word "Cheating"; without the heading
        # a query naming the offence cannot match it.
        chunk = chunk_section_whole([make_section()])[0]
        assert "Section 420" in chunk["text"]
        assert "Cheating" in chunk["text"]

    def test_skips_empty_sections(self):
        assert chunk_section_whole([make_section(text="   ")]) == []


class TestSectionSplit:
    def test_short_section_stays_whole(self):
        chunks = chunk_section_split([make_section()])
        assert len(chunks) == 1
        assert chunks[0]["chunk_count"] == 1

    def test_long_section_is_split(self):
        long_text = " ".join(f"This is sentence number {i}." for i in range(200))
        chunks = chunk_section_split([make_section(text=long_text)])
        assert len(chunks) > 1

    def test_every_sub_chunk_repeats_the_heading(self):
        long_text = " ".join(f"This is sentence number {i}." for i in range(200))
        for chunk in chunk_section_split([make_section(text=long_text)]):
            assert "Section 420" in chunk["text"]

    def test_sub_chunks_share_section_identity(self):
        long_text = " ".join(f"This is sentence number {i}." for i in range(200))
        chunks = chunk_section_split([make_section(text=long_text)])
        assert {c["section"] for c in chunks} == {"420"}
        assert len({c["chunk_id"] for c in chunks}) == len(chunks)

    def test_sub_chunks_keep_pointer_to_full_section(self):
        long_text = " ".join(f"This is sentence number {i}." for i in range(200))
        for chunk in chunk_section_split([make_section(text=long_text)]):
            assert chunk["section_text"] == long_text


class TestFixedWindowBaseline:
    def test_produces_windows(self):
        sections = [make_section(str(n), text=" ".join(["word"] * 300)) for n in (1, 2, 3)]
        assert len(chunk_fixed_window(sections)) > 1

    def test_windows_can_span_section_boundaries(self):
        """The documented weakness of the baseline, asserted rather than assumed."""
        sections = [
            make_section("1", text=" ".join(["alpha"] * 150)),
            make_section("2", text=" ".join(["beta"] * 150)),
        ]
        chunks = chunk_fixed_window(sections)
        spanning = [c for c in chunks if "alpha" in c["text"] and "beta" in c["text"]]
        assert spanning, "expected at least one window to cross a section boundary"


class TestRealCorpus:
    """Guards against silent corpus regressions. Requires the parsed corpus."""

    @pytest.fixture(scope="class")
    def chunks(self):
        try:
            return build("section_whole")
        except FileNotFoundError:
            pytest.skip("parsed corpus not built; run backend.ingestion.parse_statutes")

    def test_chunk_ids_unique(self, chunks):
        ids = [c["chunk_id"] for c in chunks]
        assert len(ids) == len(set(ids))

    def test_both_statutes_present(self, chunks):
        assert {c["document"] for c in chunks} == {"IPC", "BNS"}

    def test_ipc_and_bns_never_share_legal_status(self, chunks):
        statuses = {c["document"]: set() for c in chunks}
        for chunk in chunks:
            statuses[chunk["document"]].add(chunk["legal_status"])
        assert statuses["IPC"] == {"repealed"}
        assert statuses["BNS"] == {"in_force"}

    def test_landmark_sections_present(self, chunks):
        present = {(c["document"], c["section"]) for c in chunks}
        for section in ("302", "420", "378", "34", "124A", "304A", "498A"):
            assert ("IPC", section) in present, f"IPC s.{section} missing from corpus"

    def test_superseded_sections_carry_a_warning(self, chunks):
        for chunk in chunks:
            if chunk["document"] == "IPC" and chunk["section"] in ("375", "376", "376A"):
                assert chunk["superseded_note"], (
                    f"IPC s.{chunk['section']} has pre-2013 text and must carry a warning"
                )

    def test_every_chunk_has_a_source_url(self, chunks):
        assert all(c["url"].startswith("https://") for c in chunks)
