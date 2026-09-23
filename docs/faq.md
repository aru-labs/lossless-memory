# FAQ — answers to the Show HN questions

I posted this project to Hacker News on 2026-09-21. Several good questions came up there.
I tried to answer one of them on the thread; the reply was flagged and hidden within a minute, and I have had no response from the moderators since. Rather than leave the questions unanswered, I am answering them here, in the repository, where nothing can be hidden.

A note on English: I am Japanese and I write with a translator's help. If something reads oddly, that is me, not an attempt at marketing copy.

---

## 0. What this is part of

This repository is one component of a larger system, and the questions below make more sense with that context.

The system is a personal AI that lives on hardware I own and is meant to stay the same partner over years, not sessions. Three things hold it up:

- **Memory** — the raw log, never summarized. That is this repository.
- **Time** — every record carries an absolute timestamp, and the whole system is threaded on that one axis. The goal is not precision for its own sake: it is that the AI lives in the same time I do. An assistant with no clock skips the middle of things — you say "I'm heading to the car" and it says goodbye, when in fact you still have to stand up, change, find your keys and walk. Sharing a clock is what removes that.
- **Warmth** — facts alone do not bring a relationship back. Personality and emotional state are injected on a separate path from memory, on purpose, so that one failing does not drag down the other.

On top of those, the design says the model must read five things **before** it speaks: the clock, the current-position index (LLL), recalled raw log (only when the turn calls for it), the personality layer, and finally an output check that refuses fabricated content. Memory is the first of the five. The output check ("EYE") is designed and running in my own house in an early form, but it is **not** in this repository and not published — calling it finished would be a lie.

So: what you see here is the memory layer, extracted and generalized. It is the part that was ready to stand on its own.

---

## 1. "Does it store everything forever? That's a lot of data."

There is a misunderstanding worth clearing up first: this system does not record screens or audio. It stores **text** — the conversation itself, one record per turn, seven fields, appended to a JSONL file per day.

Text is cheap. The expensive part was never the raw log; it was the semantic index built on top of it. Measured on the running instance:

| | |
|---|---|
| Vector store, after removing library contamination | 2.54 GB → 337 MB |
| Vector rows | 865,588 (at its worst) → 124,174 |
| Exact-index rebuild | 40 s → 1.24 s |

The rule that keeps this manageable: **only the raw log is sacred.** Every index — exact, semantic, document — is derived, and any of them can be deleted and rebuilt from the log. If disk becomes a problem, you delete indexes, not memories. Old raw logs can be moved to slower storage; they are plain files.

What I do **not** do is prune "redundant" turns. Deciding what is redundant is exactly the decision this project refuses to hand to a model.

## 2. "The relative-time parser is Japanese-only. Wiring up dateparser or duckling would take an evening — leaving that on the roadmap is an odd choice."

That is a fair hit, and it is correct.

Relative expressions ("last night", "three days ago") are parsed in Japanese only. In English you currently need absolute dates. The reason is honest but not much of a defense: this ran for one Japanese-speaking user for months, and the parser grew from what that user actually said out loud, including speech-recognition misreadings.

I am taking this as a fix rather than a roadmap item. The time phrase is the one thing this system cannot afford to be second-rate at, since time is what narrows the search before ranking happens. Adding a general parser for English relative phrases is now on the list ahead of new features.

## 3. "Put LLL inside the user message right before the new query — it keeps the conversation prefix intact."

This is the most useful comment in the thread and I am grateful for it.

Today LLL is injected by the host's per-turn hook, which puts it ahead of the turn as a separate block. If the block changes every turn, the shared prefix changes with it, and prompt caching suffers. Moving the injection inside the user message, immediately before the new question, leaves the earlier prefix untouched and keeps the cache.

That is a change to the injection adapter, not to the index, so it is cheap to make. It is on the list.

## 4. "How is this different from obra/episodic-memory?"

They overlap in storage and differ in what they trust.

episodic-memory indexes conversations from several agent tools, retrieves mainly by semantic similarity, and produces summaries for display. It is built to be a plugin across platforms.

This project starts from the opposite end:

- **Time first, words second.** A time phrase narrows the range before anything is ranked. Semantic search is the last resort, and when it is used, the output header says so.
- **No summarization anywhere.** Not for display, not for indexing. A summary would become the version people read, and the raw line would stop being read at all.
- **A current-position index (LLL)** injected every turn, so the assistant survives a context compaction without starting the relationship over. As far as I know, that layer has no equivalent there.
- **One person, one assistant, one machine.** No multi-tool story. That narrowness is the point, not a gap.

If you want semantic recall across many tools, theirs is the more practical answer today. If you want the exact lines from last Tuesday night, in order, this one.

## 5. "When the AI's derived memory changes, how can a user inspect the original? Is chronology becoming more important than semantic summaries?"

On the first half: the AI never derives or edits memory here. There is no writing path from the model to the log. The model reads; the recorder writes, and the recorder contains no model — it is plain deterministic code that converts a turn into seven fields. So there is no "derived memory" to reconcile with an original. Indexes can be corrupted or contaminated (mine were, badly, and it is documented in `lessons.md`), but they are rebuilt from the log and the log is untouched.

On the second half: for a personal assistant, yes. Semantic summaries answer "what kind of thing did we discuss." Chronology answers "what did you actually say to me, and when" — and that is the question that decides whether a long-running assistant still feels like the same one. Versioning is not the right frame either: nothing is versioned because nothing is rewritten.

## 6. "You could almost design a small DSL and not need AI at all."

Half agreed, and it is a sharper observation than it looks.

Retrieval here already contains no AI. Parsing the time phrase, narrowing the range, ranking by exact match, reading neighbours in order — all of it is ordinary code. A DSL would cover that part fine.

The AI is needed at two edges. One is the front: people speak in "yesterday evening, around when we talked about the budget," not in query syntax, and turning that into a range is the messy part — especially from speech recognition. The other is the back: the assistant that receives the lines has to answer with them instead of inventing something adjacent. Those two edges are why the memory exists at all.

---

## Status (2026-09-23)

Active development of this repository is **paused for a while** for personal reasons — my available compute budget changes from this week, and the remaining capacity goes to the assistant this memory belongs to.

That means:

- The repository stays up and the system stays in daily use. It is not abandoned.
- Issues and questions are welcome, but replies may be slow.
- The two accepted fixes above (English relative dates, LLL injection position) are queued; I am not promising a date, because I have learned what happens when I promise dates.

Thank you to everyone who read it and asked something real.
