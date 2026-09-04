# LLL — Left Leg Layer

LLL (Left Leg Layer) is a lightweight, real-time index of "what topic are we in right now," injected into the model on every turn so it can follow a conversation partner whose attention jumps around, without running a search. A human collaborator marks priority on entries in this index; the AI only reads it.

## Why "left leg"

The name comes from an internal metaphor rather than a technical acronym expansion: the right leg stands for statistical prediction — trying to forecast where a conversation is going next — and the left leg stands for simply knowing, at any instant, where you're standing right now and following from there, one step at a time, even when the steps are discontinuous. This layer is the second approach: it doesn't predict the next topic, it just keeps an accurate, cheap index of the current one so the model doesn't have to guess.

Conceptually it is modeled on hippocampal memory indexing: detailed memory (the raw conversation) lives in one place, and a separate, much lighter index of "when did we talk about what" lives alongside it, cross-referenced by time. LLL is that index for the currently active conversation.

## How the index is built

- **No separate database.** The index is computed directly from the live conversation transcript every time it's needed — there is nothing to keep in sync, and nothing that can drift stale.
- **Tail-only reads.** Each source transcript is read from its tail only, in a fixed amount, not in full — keeping computation fast enough to run on every single turn without being noticeable (well under a second).
- **Cross-session merge.** Because the underlying conversational identity is continuous across sessions, the index is built from every session active within a recent window, merged onto one timeline by timestamp, rather than from a single session in isolation. This is what lets a freshly started session pick up the current topic instead of starting blank.
- **Chunking by actual conversational rhythm.** A new "chunk" of topic starts when there's a several-minute gap in speech, or after a bounded amount of continuous time on one apparent thread — not on any fixed schedule.
- **Headline extraction, not summarization.** The headline for each chunk is drawn mechanically from the densest clause of the actual utterance — the clause richest in concrete nouns and specific terms — rather than generated or paraphrased by a model. No wording is changed; a clause is only selected. This is a deliberate choice: an index that summarizes reintroduces exactly the information loss the rest of the memory system is built to avoid. Where the clause used isn't the first one in the utterance, that's marked, so the excerpt is never confused for verbatim opening text.
- **Folding for length control.** Recent activity (e.g. the last hour) is kept as one entry per topic chunk in full; anything older than that is thinned to at most one representative entry per fixed time bucket (e.g. every 30 minutes), so the index doesn't grow without bound over a long session. Folding only affects the index — nothing is removed from the underlying raw log, which remains fully readable at any time.
- **A capped, real-time injection window.** What actually gets injected into the model every turn is bounded to a recent window (on the order of a few hours), not the entire index — deep history is the job of the search-based recall layers, not this one. If entries had to be cut to fit, the injection says so explicitly (a one-line "N earlier entries omitted") rather than silently dropping them.
- **Marking notably charged moments.** A small, rule-based keyword check flags utterances that read as a firm decision, a promise, or a strong correction, and those are always surfaced as their own line — even if they land in the middle of what would otherwise be folded away — so an important moment doesn't get buried by the same folding that keeps the index short.

## Priority: a human-only channel

Beyond the automatically generated topic index, entries can carry a priority mark. This is the one part of LLL that is explicitly not the AI's to touch:

- A small number of priority tiers exist, from a top tier reserved for only the most safety- or survival-critical items, down through several ordinary priority levels, to a low tier meaning "parked — not urgent, don't forget." There is also a separate one-off "flag" mark meant to grab the AI's attention specifically, and a "done" mark.
- **Only a human sets, changes, or clears these marks.** The AI's role is to read them and internalize what the human currently considers important — not to triage, not to mark things resolved on its own judgment, and not to remove a mark even if the underlying task looks completed or overdue. An item without a priority mark, by contrast, is implicitly out of the human's current attention and can be treated as settled.
- To keep the per-turn injection bounded, only the small top-priority tier is injected without a length limit; every other tier is capped to a handful of its most recent entries. A human-facing view of the same data has no such cap and shows everything — the AI's per-turn view is deliberately a narrower window onto a larger store, and it should not assume it has seen "all of it" just because nothing more was injected.

## Who writes topic entries

New topic entries are not appended automatically on every subject change. The index is meant to reflect what the human actually cares about tracking, and an automatic-append policy tends to flood it with low-signal entries that then bury the ones that matter. Instead:

- The AI adds an entry only when explicitly asked to.
- The human can add one directly through their own interface to the same log.
- A "done" mark can be applied to entries matching a given phrase, without altering the original entry text — the system does not delete or rewrite history, it only layers a separate completion marker on top of it.

## What LLL is not

It is not a task list and not a place to track deadlines — priority marks reflect what a human currently finds important to keep in view, not a queue to be worked through and closed out. It is also not the system's only memory of the past: it is a short, cheap, always-on index of *recent* context, deliberately capped to keep both computation and the model's attention window small. Anything older, or anything the current index doesn't happen to surface, is the job of the search-based recall layers described in `docs/memory-system.md`.
