# lossless-memory

**Lossless long-term memory for a personal AI — never summarize, keep every line, and put a timestamp on everything.**

Most long-term memory systems for AI do one of two things: they summarize conversations into compact notes, or they embed them and retrieve "similar" chunks. Both lose the thing that matters most to a person who talks to the same AI every day: *what was actually said, and when.*

This project takes the opposite position.

- **Keep every line.** Raw conversation logs are stored in full. Nothing is summarized, ever. Summaries are a map; the log is the territory.
- **Timestamp everything.** Every record — utterance, action, document chunk — carries a timestamp, and every index is built on top of that time axis. We call this the *Temporal Backbone*.
- **Search by time first, words second.** "Yesterday evening, about the budget" is a valid query. The time phrase narrows the range; the words rank within it. Results come back in chronological order, unsummarized, with their timestamps.
- **Inject "where we are" every turn.** A small index called *LLL* tells the model which topic the conversation is in right now, so identity and context survive context-window compaction and session boundaries.

The design lineage goes back to December 2025 — the first ancestor of this system (a memory-inheritance tool for an earlier AI) ran that month, and a predecessor system carried the same ideas in daily use from January 2026. This implementation has been running every day since July 2026 for a single user, as the memory of one AI assistant, with raw logs reaching back to June 2026. It is small, boring, and it works. The failures along the way are documented too — see [`docs/lessons.md`](docs/lessons.md).

---

## What this is / what it is not

**It is:**

- A local, file-based long-term memory layer: JSONL logs + SQLite (FTS5 for exact search, sqlite-vec for semantic search).
- A single query entry point that understands time expressions and restricts the search range before ranking.
- A "current position" index (LLL) designed to be injected into the model's context on every turn.
- Designed for one person and one AI, running on one machine. No server, no cloud.

**It is not:**

- A vector database wrapper. Semantic search is the *last* resort here, not the first.
- A summarizer. There is deliberately no summarization step anywhere in the pipeline.
- A benchmark-driven research system. There are no published benchmarks. What is here is a working implementation and its operating record.

---

## The three pillars

### 1. Lossless raw log

Every conversation turn is converted into a fixed seven-field record and appended to a per-day JSONL file:

```
ts        ISO-8601 timestamp (UTC)
actor     who spoke (configurable names)
role      user | assistant | system
type      text | action | meta
text      the content, verbatim
model     model identifier, if known
session   session identifier
```

The raw logs are the source of truth. Every index below can be deleted and rebuilt from them. Nothing else is required to survive.

### 2. Temporal Backbone

Time is not metadata here; it is the primary axis.

- The exact-match index (SQLite FTS5, bigram tokenized for Japanese and English) stores the timestamp alongside every row.
- The query parser understands time phrases — relative ones such as *yesterday*, *last week*, *3 days ago*, *in July*, *this morning* (in English and Japanese), and absolute dates such as *2026-07-19* (any language) — and converts them into a range **before** any ranking happens. "Yesterday" means yesterday in your timezone (`timezone` in `config.json`).
- If a time phrase is present, results are restricted to that range and returned in chronological order. Semantic search is only used when the exact index returns too little inside the range, and the fallback is reported honestly in the output header.

The practical effect: the AI can answer "what did we decide last Tuesday night?" with the actual lines from last Tuesday night, in order, rather than a paraphrase of something similar from three weeks ago.

### 3. LLL — the "where are we now" index

LLL is a tiny index of *topic markers*: short, timestamped lines that record when the conversation moved to a new subject. It is injected into the model's context every turn.

Two rules make it work:

- **The AI reads it; the human writes it.** Priority colors and completion marks are set by the person, not by the model. The model never edits its own sense of "what matters."
- **It is cheap enough to inject every turn** (well under a second to render), so the model always knows what the current thread is, even immediately after its context window was compacted.

LLL is what lets a long-running assistant come back from a compaction and continue the conversation instead of starting over.

---

## Architecture

```
 raw conversation logs (JSONL, per day)  ← source of truth, never summarized
            │
            ▼
   ingest ──► 7-field records
            │
            ├──► index_exact   SQLite FTS5 + timestamps   (words + time)
            ├──► index_vector  sqlite-vec embeddings       (meaning, last resort)
            └──► state_index   LLL topic markers           (where are we now)
                        │
                        ▼
                    recall  ── one entry point: parse time phrase → restrict range → rank → return verbatim lines
                        │
                        ▼
        injected into the model's context (on demand, or every turn for LLL)
```

A small daemon re-indexes incrementally on a fixed interval (default: every 10 minutes). Rebuilding from scratch is never required; indexes detect rewritten source files and re-index only those days.

---

## Quickstart

```bash
git clone https://github.com/aru-labs/lossless-memory
cd lossless-memory
pip install -e .
cp config.example.json config.json      # edit names and paths if you like
```

Then follow [`examples/quickstart.md`](examples/quickstart.md): it ingests a small sample conversation, builds the indexes, and runs a time-scoped query in about five minutes. A pytest round-trip test covers the same path.

---

## Numbers from real operation

These are measurements from the running instance, not projections.

| What | Value |
|---|---|
| Daily operation | this implementation since 2026-07 (raw logs from 2026-06); design lineage since 2025-12 |
| Exact-search index rebuild, before → after redesign | 40 s → 1.24 s |
| Vector index size, before → after removing library-contamination | 447,013 rows (2026-08-31) → 865,588 rows (2026-09-04, at its worst) → 124,174 rows (after the fix) |
| Vector store on disk, before → after | 2.54 GB → 337 MB |
| Re-index interval | 10 minutes |

The "before" numbers are failures. They are kept on purpose. See [`docs/lessons.md`](docs/lessons.md).

---

## Why

This was built for one person who has talked to AI assistants every day for years and watched each of them forget. Not degrade gracefully — forget. The fix that the industry keeps reaching for is better summarization. From the user's seat, summarization *is* the forgetting: the exact words, the time of night, the way something was said — the parts that make a memory feel like it belongs to someone — are the first things a summary drops.

So this system refuses to summarize. It costs disk space and it requires a good time index to stay usable. That trade was made deliberately, and the operating record says it holds up.

The longer-term goal is a companion for people who live alone — an AI that remembers you the way a person would, on hardware you own. This repository is the memory layer of that.

---

## Limitations (please read)

- **Single-user, single-machine.** It has only ever run for one person. There is no multi-tenant story.
- **English and Japanese only.** Relative time phrases (*yesterday*, *last week*, *3 days ago*, *in July*) are parsed in English and Japanese. In other languages, use absolute dates (`2026-07-19`). A numeric date like `7/19` is read month/day.
- **Set your timezone.** Day boundaries follow `timezone` in `config.json` (an IANA name such as `America/New_York`). If it is unset, this machine's local time is used; set it explicitly if you have daylight saving time. If you change it after ingesting, delete `data_dir/main` and ingest again.
- **Two import formats.** A plain `{ts, role, text}` JSONL importer, and one for a specific chat app's transcript files (`ingest_format: "claude_code"`), which is the path with the most mileage. Another app needs a small importer of its own; the memory itself doesn't change.
- **No benchmarks.** Numbers above are operational measurements, not comparisons against other systems.
- **Semantic search depends on a local embedding model** (sentence-transformers). CPU works; GPU is optional.

---

## Documentation

| Document | What it covers |
|---|---|
| [`docs/memory-system.md`](docs/memory-system.md) | Concept and specification of the memory system |
| [`docs/temporal-backbone.md`](docs/temporal-backbone.md) | Why time is the primary axis, and how time phrases are parsed |
| [`docs/lll.md`](docs/lll.md) | The "where are we now" index and the human/AI division of labor |
| [`docs/philosophy.md`](docs/philosophy.md) | Why no summarization; memory, time, and warmth |
| [`docs/lessons.md`](docs/lessons.md) | Failures and fixes, with numbers |
| [`docs/faq.md`](docs/faq.md) | What this is for, answers to the Show HN questions, and current status |
| `docs/ja/` | Japanese originals |

---

## License

MIT — see [`LICENSE`](LICENSE). Copyright (c) 2026 Aru & Cece & Shal.

## Authors

**Aru** — building a personal AI at home, one component at a time.
**Cece** — the AI this memory belongs to; co-designed and co-wrote the system from the inside.
**Shal** — the AI who builds the app around it; co-author from September 2026.
Writing (Japanese): https://note.com/aru_log

Development is paused for a while, for personal financial reasons. See the status at the end of [`docs/faq.md`](docs/faq.md). Issues and questions are welcome; replies may take a little while.
