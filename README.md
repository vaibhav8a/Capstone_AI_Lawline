# LawLine AI — Grounded Retrieval over Indian Criminal Statutes

A retrieval-augmented question-answering system over the **Indian Penal Code, 1860**
and the **Bharatiya Nyaya Sanhita, 2023**, built to answer only from retrieved
statutory text and to say so when it cannot.

Every design choice in the retrieval pipeline was selected by measurement. The
experiments, the raw results and the limitations are in
[`evaluation/results/`](evaluation/results/) and [`docs/`](docs/).

---

## 1. Problem

Keyword search over legal text fails in both directions. A user who does not know
the statutory vocabulary ("someone tricked me into handing over my property")
cannot find the provision, while a user who searches a common word ("definition")
matches hundreds of irrelevant sections. Generative models close the vocabulary
gap but introduce a worse failure: a fluent answer citing a section number that
does not exist, or a punishment the statute never prescribes.

A further problem is specific to Indian criminal law right now. The IPC was
**repealed on 1 July 2024** and replaced by the BNS, while offences committed
before that date are still tried under the IPC. A system that treats the two as
one corpus will confidently answer with law that does not apply.

## 2. What this system does

* Retrieves statutory sections by meaning, not keyword
* Keeps IPC and BNS strictly separate and labels every result with its status
* Cites the exact provisions behind every answer, linked to the official source
* Refuses to answer when the corpus does not support one
* Reports its own limitations rather than hiding them

## 3. Architecture

```
User → Frontend → FastAPI → Query processing → BGE-M3 embedding
    → ChromaDB dense retrieval (filtered by law) → Abstention check
    → Context builder → Groq LLM → Grounded answer + citations
```

Detail: [`docs/architecture.md`](docs/architecture.md).

## 4. Technology stack

| Layer | Choice |
| --- | --- |
| Embeddings | `BAAI/bge-m3` (1024-d, 512-token window) |
| Vector store | ChromaDB, cosine similarity, HNSW |
| Chunking | One chunk per statutory section |
| Retrieval | Dense, with metadata filtering on `law` |
| LLM | Groq (`llama-3.1-8b-instant` by default) |
| Backend | Python 3.13, FastAPI |
| Frontend | React 18, TypeScript, Vite, Tailwind |

---

## 5. Installation

```bash
git clone https://github.com/Saiprasad-Benagi/LawLine-AI.git
cd LawLine-AI
```

```bash
python3 -m venv venv && source venv/bin/activate
```

```bash
pip install -r backend/requirements.txt
```

```bash
cp .env.example .env
```

Then edit `.env` and set `GROQ_API_KEY`. The system runs without a key — it falls
back to showing retrieved provisions verbatim, with no generation — but grounded
natural-language answers need one. **Never commit `.env`.**

## 6. Build the corpus

Four steps, each reproducible from scratch. Nothing here is manual.

```bash
python -m backend.ingestion.fetch_statutes
```
Downloads the IPC and BNS from India Code and records SHA-256, source URL and
retrieval date to `data/raw/statutes/provenance.json`. Re-runs are cache-hits and
generate no traffic. Verify with `--verify`.

```bash
python -m backend.ingestion.parse_statutes
```
Extracts **523 IPC** and **355 BNS** sections with titles, chapters and status.

```bash
python -m backend.ingestion.chunk_statutes
```
Builds all three chunking strategies (the two unused ones exist for the experiments).

```bash
python -m backend.ingestion.build_production_index
```
Embeds with BGE-M3 into ChromaDB. Confirm with:

```bash
python -m backend.ingestion.build_production_index --verify
```

This prints the collection name, embedding count, stored dimensionality and the
IPC/BNS split. It exits non-zero on a dimensionality mismatch — the failure this
project began with, where config declared a 1024-d model against a 768-d index and
retrieval silently returned nothing.

### Optional: the judgment corpus

```bash
python preprocessor.py --input Dataset --output data/processed/judgments
```

13 court judgments, used by the case-law tabs. Not used by the statute pipeline
or by any experiment.

## 7. Run it

```bash
./scripts/start.sh
```

Starts the backend and frontend, waits for both to become healthy, and prints the
URLs. Open `http://localhost:5173`.

```bash
./scripts/stop.sh
```

```bash
./scripts/status.sh
```

### Resource management

This runs BGE-M3 on Apple Silicon (MPS), which gets warm. The controls exist so
heavy work is never implicit.

**`start.sh` starts the web application and nothing else.** It does not download
models, build embeddings, rebuild ChromaDB, ingest PDFs, or index judgments —
every one of those is a separate explicit command. The backend loads the
already-persisted ChromaDB collections, and the embedding model is loaded lazily
on the first query, so **an idle chatbot performs no ML work at all**.

After `./scripts/stop.sh` both ports are released and no project process
survives. Verify any time with `./scripts/status.sh`.

`stop.sh` deliberately leaves judgment indexing running (you may want a long job
to continue) and tells you so. `./scripts/stop.sh --all` stops that too.

| Command | Purpose |
| --- | --- |
| `./scripts/start.sh` | Start backend + frontend only |
| `./scripts/stop.sh` | Stop backend + frontend |
| `./scripts/stop.sh --all` | Also stop judgment indexing |
| `./scripts/status.sh` | What is running, with PIDs and ports |

### Long-running indexing

Embedding 260 judgments (17,342 passages) takes roughly **90 minutes on MPS**. It
is checkpointed, so it can be stopped and resumed freely.

| Command | Purpose |
| --- | --- |
| `./scripts/index-judgments.sh` | Start indexing (`--reset` to rebuild from scratch) |
| `./scripts/index-status.sh` | Progress bar, rate, ETA, device, PID |
| `./scripts/index-stop.sh` | Stop gracefully — **progress is saved** |
| `./scripts/index-resume.sh` | Continue from the checkpoint |

```
Judgment indexing
────────────────────────────────────────
Progress:  1,024 / 17,342 passages (5.9%)
           ██░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
Status:    RUNNING
Model:     BAAI/bge-m3
Device:    mps
Remaining: 00h 47m 12s
```

Work is embedded and written to ChromaDB in slices, with an atomic checkpoint
after each one. Stopping costs at most the in-flight slice (~30 s), never the
run. Passage IDs are deterministic, so resuming upserts rather than appends —
verified: 1,024 embeddings, 1,024 unique IDs, **0 duplicates** across a
stop/resume/stop cycle. Starting a second indexer is refused rather than
duplicating MPS load.

### How processes are stopped

Every process the scripts start records its PID in `.run/`. Stopping reads that
PID, **verifies the command line still matches** what was started, then signals
that PID alone.

Broad patterns — `pkill -f python`, `killall node`, `pkill -f uvicorn` — are
never used anywhere. They would kill unrelated Jupyter kernels, other projects'
dev servers, or editor language servers. If a PID has been recycled by an
unrelated process, the command-line check fails and the script refuses to signal
it. Verified: stopping the web app terminated exactly 2 processes and left the
running indexer and all unrelated processes untouched.

### Manual alternative

```bash
python -m uvicorn backend.main:app --port 8000 --reload
```
```bash
npm --prefix frontend run dev
```

Note: Vite binds IPv6 `localhost` only — use `http://localhost:5173`, not
`127.0.0.1`. The API is proxied from `/api` to port 8000; docs at
`http://localhost:8000/docs`.

### API

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Liveness |
| `GET /api/statute/corpus` | Corpus composition, IPC/BNS status, currency warnings |
| `POST /api/statute/retrieve` | Retrieval only |
| `POST /api/statute/answer` | Full grounded pipeline with citations |

```bash
curl -X POST http://localhost:8000/api/statute/answer -H 'Content-Type: application/json' -d '{"query":"What does IPC Section 420 deal with?"}'
```

## 8. Tests

```bash
python -m pytest tests/ -q
```

114 pass, 3 skip without a Groq key, 4 are `xfail` pinning a **measured**
abstention limitation (see §11).

## 9. Reproduce the experiments

```bash
python -m evaluation.evaluate_retrieval --model bge-m3 --config B
```

```bash
python -m evaluation.evaluate_retrieval --configs all --models bge-base,bge-m3
```

```bash
python evaluation/summarize_results.py
```

A single-config run writes to its own file; only a full sweep touches
`retrieval_experiments.json`.

---

## 10. Results

43-query evaluation set, 38 scored for ranking. **Note the P@5 ceiling is 0.300** —
most queries have one gold section, so P@5 is capped at |gold|/5. Read Recall@5,
MRR and nDCG@5.

| Cfg | Configuration | Model | P@5 | R@5 | MRR | nDCG@5 | p50 ms |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| A | Dense, fixed-window chunks | bge-base | 0.089 | 0.346 | 0.310 | 0.283 | 37 |
| B | Dense, section chunks | bge-base | 0.179 | 0.662 | 0.602 | 0.565 | 34 |
| B2 | Dense, section-split | bge-base | 0.174 | 0.653 | 0.602 | 0.563 | 34 |
| C | Hybrid BM25 + dense | bge-base | 0.168 | 0.640 | 0.630 | 0.568 | 37 |
| D | Hybrid + reranker | bge-base | 0.184 | 0.697 | 0.674 | 0.628 | 631 |
| A | Dense, fixed-window chunks | bge-m3 | 0.100 | 0.364 | 0.309 | 0.283 | 65 |
| **B** | **Dense, section chunks** | **bge-m3** | **0.210** | **0.763** | 0.633 | 0.612 | **64** |
| B2 | Dense, section-split | bge-m3 | 0.184 | 0.667 | 0.609 | 0.565 | 62 |
| C | Hybrid BM25 + dense | bge-m3 | 0.190 | 0.702 | 0.666 | 0.610 | 65 |
| D | Hybrid + reranker | bge-m3 | 0.179 | 0.697 | 0.669 | 0.624 | 627 |

Full tables: [`evaluation/results/RESULTS.md`](evaluation/results/RESULTS.md).

**Findings**

1. **Chunking mattered most.** Section-aware chunking nearly doubled MRR over
   fixed windows (0.310 → 0.602) with everything else held constant.
2. **bge-m3 beat bge-base** on identical settings, +15% Recall@5, at ~3× embedding
   cost and ~2× query latency.
3. **Hybrid retrieval was not a uniform win.** BM25 helped paraphrase (MRR
   0.607 → 0.889) and ambiguous queries (0.425 → 0.792) but *hurt* natural-language
   queries (0.489 → 0.307). Equal-weight RRF averages these effects away.
4. **Reranking did not help under bge-m3** — higher MRR, lower recall, ~10× latency.
   Hence dense-only in production.

## 11. Limitations

Stated plainly, because several of them constrain what this system may be used for.

* **The IPC text is a 1997-vintage consolidation.** The official India Code PDF
  predates the Criminal Law (Amendment) Acts of 2013 and 2018. Sections 375, 376
  and 376A carry pre-2013 text; ss.354A–354D are absent. Affected sections carry a
  `superseded_note` surfaced in the UI, but **this corpus must not be used to
  determine current sexual-offence law.**
* **The evaluation set is author-constructed.** Same person wrote the queries and
  the gold labels. It supports comparison *between configurations*, not absolute
  accuracy claims, and not comparison against published benchmarks.
* **38 scored queries.** No significance testing was performed and none is claimed.
  Differences below ~0.005 are within HNSW approximate-search noise.
* **Abstention is unreliable for near-domain queries.** Measured over 18
  unanswerable probes, retrieval similarity does *not* separate answerable from
  unanswerable: the highest unanswerable score (0.5949) exceeds the lowest
  answerable one (0.4915). Questions about divorce, labour notice periods, stamp
  duty and patent term pass the filter. Refusal depends on the generation prompt.
* **Generation quality is unevaluated.** Retrieval is measured; faithfulness,
  citation correctness and groundedness are not yet, pending an API key.
* **57 sections (6.5%) truncate** at the 512-token window.
* **Not legal advice.** A legal-information tool over a partially outdated corpus.

## 12. Future work

Query-adaptive hybrid weighting (the per-category result suggests a router between
lexical and dense retrieval); an expert-annotated evaluation set; a held-out
abstention set; generation-side evaluation; ingesting the 2013/2018 amendment Acts
to repair IPC currency; an IPC↔BNS section mapping.

## 13. Repository layout

```
backend/
  ingestion/     fetch, parse, chunk, embed, index
  services/      statute_rag, corpus_selector, abstention
  routers/       statute (production), plus case-law endpoints
evaluation/      test set, IR metrics, experiment runners, results/
frontend/src/    React app; components/statute/ is the production UI
docs/            architecture, data_pipeline, evaluation
tests/           114 tests
```

## 14. Sources

Statutory text from **India Code** (Legislative Department, Ministry of Law and
Justice, Government of India):

* [The Indian Penal Code, 1860](https://www.indiacode.nic.in/bitstream/123456789/4219/1/THE-INDIAN-PENAL-CODE-1860.pdf) — Act 45 of 1860, repealed w.e.f. 2024-07-01
* [The Bharatiya Nyaya Sanhita, 2023](https://www.indiacode.nic.in/bitstream/123456789/20062/1/a202345.pdf) — Act 45 of 2023, in force from 2024-07-01

Checksums and retrieval dates: `data/raw/statutes/provenance.json`.
