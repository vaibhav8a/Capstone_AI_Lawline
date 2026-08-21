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
candidates in; at its floor it stops exerting pull. Held-at-800 is the state
the 1,500 expansion starts from.

| Topic | Floor | Held at 800 | Status |
| --- | --- | --- | --- |
| `sexual_offences` | 80 | 109 | met |
| `sentencing` | 80 | 188 | met |
| `common_intention` | 80 | 226 | met |
| `cheating` | 60 | 96 | met |
| `theft_robbery` | 60 | 124 | met |
| `conspiracy` | 60 | 159 | met |
| `quashing` | 60 | 134 | met |
| `circumstantial` | 60 | 118 | met |
| `pocso` | 60 | 24 | **short by 36** |
| `ndps` | 60 | 44 | **short by 16** |
| `attempt` | 50 | 104 | met |
| `abetment` | 50 | 71 | met |
| `breach_of_trust` | 50 | 68 | met |
| `dowry_cruelty` | 50 | 81 | met |
| `dying_declaration` | 50 | 53 | met |
| `confession_recovery` | 50 | 56 | met |
| `new_codes` (BNS/BNSS/BSA) | 45 | 23 | **short by 22** |
| `corruption` | 40 | 88 | met |
| `uapa` | 30 | 36 | met |
| `juvenile` | 25 | 20 | **short by 5** |
| `pmla` | 25 | 19 | **short by 6** |

The `new_codes` floor was **revised from 100 to 45** after the 1,500-expansion
audit measured what the source can actually supply. BNS density among retained
judgments is 4.9% (2024), 6.2% (2025) and 33.3% (2026) — and 2026 holds only
about 102 unexamined rows in the entire dataset. Exhausting all of 2024-2026
under the existing quality bar yields roughly 45-50 such judgments, and going to
a 2,000-judgment corpus would add essentially none, because the constraint is the
source rather than the harvest. Reported judgments applying BNS/BNSS/BSA do not
yet exist in volume: most 2024-25 offences predate the 1 July 2024 commencement.

Leaving the floor at 100 would not have produced BNS coverage. It would only have
kept the quota-rescue tier permanently open on `new_codes`, admitting weaker
matter in pursuit of a number that does not exist in the corpus of reported
decisions. The floor states what is achievable and the harvest reports what was
actually found.

### Ceilings

Ceilings are a **share of the corpus target**, not an absolute count:

| Topic | Share | At 800 | At 1,500 |
| --- | --- | --- | --- |
| `murder` | 50% | 400 | **750** |
| `bail` | 50% | 400 | **750** |

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
| `bns_era_deepen` | 2024–2026 | 150 | The only band where BNS/BNSS/BSA judgments exist. 2026 is 33% new-codes but holds ~102 unexamined rows — the source's ceiling, not a choice. |
| `recent_topic_hunt` | 2018–2023 | 200 | Sole home of POCSO (7.6% of retained) and PMLA (2.7%); NDPS runs 8.5%. Both are **0%** before 2018. |
| `mid_2001_2017` | 2001–2017 | 220 | Thinnest band — 144 judgments across 16 years — and the highest acceptance measured (41.6%). Includes **2017**, which the previous stratum filled its target before reaching and never fetched. |
| `historical_1950_1972` | 1950–1972 | 80 | 23 years at zero coverage; the corpus began at 1973. Early IPC jurisprudence remains good law even where the CrPC 1898 procedure around it does not. |
| `deepen_1973_2000` | 1973–2000 | 50 | Modest depth in the founding years. Held small deliberately: this band measures 13.8% acceptance, the lowest of any. |
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
