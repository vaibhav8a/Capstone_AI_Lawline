# Judgment Corpus — Selection Criteria

What decides whether a Supreme Court judgment enters this corpus, why each rule
exists, and what the rules were measured against. The rules themselves live in
[`backend/ingestion/corpus_selection.py`](../backend/ingestion/corpus_selection.py);
this document is the reasoning behind them.

The corpus is **not** a sample of Supreme Court output. It is a criminal-law
corpus assembled for a criminal-law retrieval system, and it is filtered
accordingly. Every rejection is recorded with its reason in the candidate ledger,
so the shape of the corpus can be audited rather than taken on trust.

---

## 1. Source

One source, unchanged since the corpus began:

| | |
| --- | --- |
| Dataset | `indian-supreme-court-judgments`, [AWS Registry of Open Data](https://registry.opendata.aws/indian-supreme-court-judgments/) |
| Original source | eCourts, `judgments.ecourts.gov.in` |
| Licence | CC-BY-4.0 |
| Maintainer | Dattam Labs |
| Layout | Year-partitioned Parquet metadata + year-partitioned English PDFs |

It is preferred over scraping `sci.gov.in` because it carries an explicit open
licence, is published for bulk access, and keeps the official citation and
neutral citation on every record so any judgment can be checked against the
court's own portal.

`case_name`, `petitioner`, `respondent`, `court`, `judgment_date`, `citation`,
`neutral_citation`, `judge` and `disposal_nature` are taken verbatim from the
published metadata. `statutes_referred`, `sections_referred` and `topics` are
extracted from the judgment text. Nothing is inferred, and nothing is invented.

## 2. Base gate

A candidate must clear both of these before anything else is considered:

| Rule | Value | Why |
| --- | --- | --- |
| `MIN_TEXT_CHARS` | 3,000 | Below this the PDF is an order sheet or a failed text extraction, not a judgment. |
| `MIN_CRIMINAL_SCORE` | 6 | The floor at which a document is criminal-law material at all. |

`criminal_score` weighs the statutes a judgment actually cites (4 points for
IPC/BNS/CrPC/BNSS, 2 for Evidence/NDPS/POCSO) plus criminal vocabulary density,
capped so a long judgment cannot pass on length alone.

## 3. Two-tier admission

Clearing the base gate is not sufficient for bulk expansion. Above the gate:

| Tier | Condition | Effect |
| --- | --- | --- |
| General fill | `criminal_score >= 10` | Admitted. |
| Quota rescue | `6 <= criminal_score < 10` | Admitted **only** if it covers a topic still below its floor. |
| — | otherwise | Rejected as `below_general_fill_score`. |

The original corpus of 260 was built at a flat threshold of 6. Tripling the
corpus at that same threshold would have tripled the weakly-relevant tail as
well. The split raises the bar for volume while keeping thin topics reachable.
The floor of 6 is retained so the existing 260 remain inside the published
criteria rather than being retroactively excluded.

## 4. Topic balance

Topics are coverage buckets for balancing, **not** legal classifications. A
judgment usually sits in several.

### Floors

Expressed as judgment counts in the corpus. A topic below its floor pulls
candidates in; at its floor it stops exerting pull. Sorted by how close each
topic sits to its floor — the ratio, not the raw count, is what identifies a
thin area worth targeting in the next expansion.

| Topic | Floor | Held at 3,000 | Status |
| --- | --- | --- | --- |
| `pocso` | 80 | 82 | met (1.0x) |
| `new_codes` (BNS/BNSS/BSA) | 75 | 85 | met (1.1x) |
| `pmla` | 25 | 56 | met (2.2x) |
| `juvenile` | 25 | 71 | met (2.8x) |
| `ndps` | 60 | 184 | met (3.1x) |
| `dying_declaration` | 50 | 170 | met (3.4x) |
| `uapa` | 30 | 127 | met (4.2x) |
| `breach_of_trust` | 50 | 214 | met (4.3x) |
| `sexual_offences` | 80 | 361 | met (4.5x) |
| `abetment` | 50 | 250 | met (5.0x) |
| `dowry_cruelty` | 50 | 251 | met (5.0x) |
| `confession_recovery` | 50 | 270 | met (5.4x) |
| `cheating` | 60 | 328 | met (5.5x) |
| `theft_robbery` | 60 | 402 | met (6.7x) |
| `circumstantial` | 60 | 427 | met (7.1x) |
| `quashing` | 60 | 467 | met (7.8x) |
| `attempt` | 50 | 413 | met (8.3x) |
| `corruption` | 40 | 333 | met (8.3x) |
| `sentencing` | 80 | 739 | met (9.2x) |
| `conspiracy` | 60 | 585 | met (9.8x) |
| `common_intention` | 80 | 912 | met (11.4x) |

`pocso` and `new_codes` are the two floors that have been revised on evidence.
Both are **source-limited**: POCSO does not appear at all in reported judgments
before 2018, and BNS/BNSS/BSA judgments exist only in 2024-2026, a band now
substantially exhausted (roughly 180 qualifying candidates remained before the
3,000 round). Their floors state what the source can actually supply rather than
a round number, because an unreachable floor would hold the quota-rescue tier
permanently open and admit weaker matter in pursuit of a number that does not
exist.

### Ceilings

Ceilings are a **share of the corpus target**, not an absolute count:

| Topic | Share | At 800 | At 1,500 | At 3,000 |
| --- | --- | --- | --- | --- |
| `murder` | 50% | 400 | 750 | **1500** |
| `bail` | 50% | 400 | 750 | **1500** |

They were absolute (400 each) while the target was 800, where 400 meant "half the
corpus". Carried unchanged to a 1,500 target the same 400 would have meant 27%,
binding early and rejecting sound murder and bail judgments for no reason other
than that the target moved.

Murder and bail dominate reported criminal appeals, so an unconstrained
expansion deepens those two and leaves the rest where it found them. A candidate
is rejected as `topic_ceiling` only when **every** topic it covers is already at
its ceiling — a murder case that also turns on a dying declaration still enters.

## 5. Strata

Allocation is computed from the **persisted corpus** — the `stratum` field on the
records — never from a counter in the running process. This is not a stylistic
choice. While building the 800-judgment corpus the counter was process-local, so
each interruption restarted every stratum's allocation at zero and `bns_era`
reached 202 against an approved 120. `tests/test_stratum_allocation.py` pins the
property down with 14 tests, including simulated runs of up to 25 interruptions.


Years are drawn in blocks chosen to close identified gaps. Targets are soft: a
stratum that runs out of admissible candidates hands its remainder to the
quota-fill pass rather than forcing weak material in to hit a number.

| Stratum | Years | Target | Why |
| --- | --- | --- | --- |
| `mid_depth_2001_2017` | 2001–2017 | 300 | The reservoir: ~5,300 qualifying candidates still unexamined at the highest acceptance rate measured (39.2%). General criminal-law depth at the lowest cost per retained judgment. |
| `pocso_depth` | 2018–2023 | 200 | POCSO sits at 63 — only 1.1x its floor, the thinnest topic in the corpus — and is 0% before 2018. ~900 qualifying candidates remain here. Also carries PMLA (2.0x) and juvenile justice (2.3x). |
| `early_depth_1950_1972` | 1950–1972 | 140 | ~1,900 qualifying candidates at 25.1% acceptance. Early IPC jurisprudence, still the thinnest era by judgment count. |
| `bns_final` | 2024, 2025, 2026 | 100 | Only ~464 unexamined rows and ~180 qualifying candidates remain in the entire 2024-2026 band. This round substantially exhausts the only source of BNS/BNSS/BSA judgments that exists. |
| `year_completion` | 1971, 1972, 1998, 1999 | 60 | Closes the final gaps in year coverage. These four years have never had a single candidate examined; ~2,150 rows sit behind them. |
| `quota_fill` | all of the above | remainder | Runs only if the strata under-deliver or floors remain short. |

Acceptance is driven by **era, not depth**. Measured by examination depth within
a year: 23.4% → 19.7% → 30.9% → 31.2% → 28.2%. There is no exhaustion curve —
harvesting deeper into a year does not degrade quality. What varies is the era:
1970s–2000s SCR volumes are dominated by tax, service and constitutional matter
(82.7% rejected as non-criminal), while 2001–2016 and 2024–2026 run 42–46%
criminal.

Within a stratum, an even per-year cap is applied first so the allocation
spreads across the years rather than being exhausted by the first one. A second
uncapped pass makes up any shortfall from years that still had material.

## 6. Download ordering — and what it deliberately is not

Downloading every candidate to discover that four in five are not criminal-law
material is wasteful, so candidates are **ordered** by a title proxy (`STATE`,
`CBI`, `POLICE`, `NCT`, `NARCOTICS`, `DIRECTORATE OF ENFORCEMENT`, …).

That proxy is never used to **exclude**. Measured against the known-criminal
260, it matches 38% of all rows but recovers only **68.7%** of genuine criminal
judgments. The 31% it misses are not marginal cases:

- *Gudikanti Narasimhulu v. Public Prosecutor* — a leading bail authority
- *Nandini Satpathy v. Dani* — self-incrimination under Article 20(3)
- every `DELHI ADMINISTRATION` prosecution, which the pattern does not name
- rows where the source data simply has a typo — `STARE OF GUJARAT`,
  `STTE OF WEST BENGAL`

So proxy-matched and non-matching candidates are interleaved 2:1, with the
non-matching set shuffled under a fixed seed for reproducibility. The
authoritative filter is always the judgment text.

## 7. Section references

`sections_referred` keeps only references a statute can be attributed to, and
emits them attributed: `IPC 302`, `Evidence 27`, `CrPC 482`.

Version 1 kept the bare number from any "Section *n*" match. Measured against
the 260, its most frequent "statutory references" were `3`, `4`, `5`, `2`, `6`
and `8` — overwhelmingly prose like "section 3 of the notification", or
paragraph numbers. A reference with no statute is not a citation.

Version 2 attaches the nearest statute mention within 120 characters. References
with no statute nearby are not dropped silently: they are kept separately in
`sections_unqualified` so the loss stays visible. Records carry
`section_extraction_version` so the two regimes are never confused.

**The original 260 records are not rewritten.** They keep their v1
`sections_referred`, because rewriting them would change the metadata attached
to 17,342 embeddings that are already built and verified. New records use v2.

## 8. Deduplication

Five keys, four of them checked **before** downloading so a duplicate costs no
bandwidth:

| Key | When | Reason recorded |
| --- | --- | --- |
| `source_url` | pre-download | `duplicate_url` |
| `(year, pdf path)` | pre-download | `duplicate_pdf_path` |
| `neutral_citation` | pre-download | `duplicate_neutral_citation` |
| `citation` | pre-download | `duplicate_citation` |
| SHA-256 of extracted text | post-download | `duplicate_text` |

The source dataset is not itself clean. Across the twelve years cached at the
time of the audit it carries roughly 50 repeated `case_id` values and 3 repeated
PDF paths, so a harvester that trusts the metadata to be unique will ingest the
same judgment twice under two rows. The text hash catches the remaining case:
the same judgment published under two different paths.

## 9. What is recorded

**`data/processed/judgments_sc/candidate_ledger.json`** — every judgment ever
examined, with the stratum that examined it, the decision, the reason, the
criminal score, the detected topics and the PDF checksum where retained. A
re-run consults the ledger and never re-downloads a known reject.

**`data/processed/judgments_sc/provenance.json`** — corpus-level provenance:
attribution, the criteria in force, per-run and cumulative harvest counters,
ledger totals with a breakdown of rejection reasons, and coverage by year, law,
stratum, statute and topic. Written atomically from the records themselves, so
it cannot drift from the corpus it describes.

**`data/raw/judgments/pdf/year=YYYY/*_EN.pdf`** — the retained source PDFs, each
with `pdf_sha256` and `pdf_bytes` on its record. Text extraction is reproducible
offline, and `--verify` re-checks every stored PDF against its checksum. Rejected
candidates' PDFs are not kept; their URL and rejection reason are enough to
reproduce the decision.

## 10. Reproducing the corpus

```bash
# What would happen, with no network access at all
python -m backend.ingestion.fetch_judgments --dry-run

# Harvest up to the target corpus size (additive; safe to re-run and to interrupt)
python -m backend.ingestion.fetch_judgments --target 800

# Re-verify metadata, text checksums, duplicates and every retained PDF
python -m backend.ingestion.fetch_judgments --verify
```

Interrupting with `Ctrl-C` or `SIGTERM` finishes the candidate in flight, saves
corpus, ledger and provenance, and exits. Re-running continues where it stopped.

## 11. Getting the corpus

The corpus itself is **not committed**. It is published as a GitHub Release asset,
because git stores gzip blobs whole with no delta compression: committing it would
add ~40 MB to the repository on every expansion, permanently, and every clone would
carry all historical versions.

```bash
./scripts/restore-corpus.sh v3000     # 41 MB download, checksum-verified
./scripts/index-judgments.sh          # rebuild the vector index (~11 h on MPS)
```

| Asset | Size | Contents |
| --- | ---: | --- |
| `judgments.json.gz` | 41.4 MB | 3,000 judgments, 139.3 M characters, 1950-2026 |
| `candidate_ledger.json.gz` | 0.7 MB | 9,350 candidates examined, with every rejection reason |

The source PDFs (1.2 GB) and the ChromaDB index (4.1 GB) are not published. Each
PDF is re-fetchable from its record's `source_url` and verifiable against the
`pdf_sha256` the record carries; the index is rebuilt from the corpus.
