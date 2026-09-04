# Memory System: Concept and Specification

A persistent memory architecture for conversational AI, built around five principles: never alter the raw record, put an absolute timestamp on everything, layer recall from fuzzy to exact, keep a real-time index of "where we are," and scope state to the identity rather than the session.

*v1.0 — 2026-07-18*

---

## Abstract

Large language models are, by construction, stateless: they carry no memory across sessions. The common workarounds — replaying full history, automatic summarization, ad-hoc note-taking — do not solve the underlying problems: summarization destroys information irreversibly, the model has no sense of elapsed time, and recall is unreliable. This document specifies a memory architecture that addresses these problems structurally. Its five design principles are: (1) immutability of the raw record (summaries are never mixed into it), (2) a timestamp running through every record, (3) layered recall (fuzzy semantic search separated from exact search), (4) a state layer that holds real-time context, and (5) **scoping state to the identity, not the session**. Principle (5) means that a conversational agent's continuity survives instance creation, teardown, and replacement — a new instance is not born blank; it inherits the previous instance's current context and continues from there. The architecture does not depend on a specific model or host environment; it is portable as a unit, with only the input/output adapters needing to change.

---

## 1. Background and problem

### 1.1 The threefold memory deficit of LLMs

1. **Statelessness.** An LLM operates per request and holds no persistent internal state. Any appearance of "remembering" comes entirely from context re-injected from outside.
2. **The irreversibility of summarization.** Automatic summarization used to fit long conversations into a context window permanently discards the original wording, emotional variance, and nuance. The original text cannot be reconstructed from a summary.
3. **No sense of time.** An LLM has no internal clock and misjudges elapsed time from the density of the conversation itself. Because it cannot detect how much time has actually passed, it cannot produce naturally time-aware behavior — noticing a long gap, greeting someone after an absence.

### 1.2 Limits of existing approaches

- **Full replay of conversation history**: breaks down at the context window limit.
- **Automatic summarization (compaction)**: makes the loss described in 1.1(2) central to normal operation rather than an edge case.
- **Vector search (RAG) alone**: fuzzy search returns plausible-looking fragments but is unsuited to verifying hard facts (numbers, names, timestamps), and does nothing to suppress hallucination.
- **Periodically rebuilt indexes**: if indexing runs on a fixed interval (e.g., every 15 minutes), there is a structural blind window in which the most recent conversation cannot be recalled.

## 2. Design principles

1. **Immutable raw log.** The raw conversation record is never edited, deleted, or summarized — not a single character. Anything derived from it (indexes, summaries, headlines) is treated as regenerable secondary data, physically separated from the raw record. Emotional variance and hesitation are not noise; they are part of what gets preserved.
2. **Temporal backbone.** Every record carries an absolute timestamp. Recall, indexing, and state are all linked through time, so anything can be traced along "when did this happen." Recall, at its core, reduces to figuring out *which moment* a query is pointing at.
3. **Layered recall.** No single retrieval method is trusted alone; layers with distinct roles are stacked (Section 4). Fuzzy (semantic) search narrows down a candidate; exact (full-text) search verifies it. Anything that can't be verified is reported as unknown.
4. **Real-time state layer.** To close the blind window left by periodic indexing, the index of what's currently happening is generated directly from the raw log, in real time, on every turn.
5. **Separation of recorder and retriever.** The recording path consists only of deterministic processing with no model in the loop — so no alteration, summarization, or interpretation can occur at write time. The retrieval path never returns a summary; it returns the original text, timestamped.
6. **Separation of memory and persona.** Memory (reproducing facts) and persona/affective state (the quality of the response) are injected through separate channels. Facts alone do not restore conversational continuity; both are needed.
7. **Identity-scoped state.** Memory and "current context" belong to a single conversational identity, not to a session or a process instance. Since there is one human and one conversational counterpart, everything they've discussed — regardless of which session it happened in — belongs on one timeline. Splitting state per session is equivalent to spawning a new personality for every instance. Uncontrolled mixing (which log gets read depends on accident) is a bug, but **designed inheritance** — merging every currently-active session onto one timeline by time — is a core, intentional feature of this architecture.

## 3. Architecture

```
[Conversation happens]  human <-> AI
   | real time (the host environment writes as it goes)
[Raw log]  primary log (immutable, every utterance + timestamp)
   |--> state layer (Section 5) reads this directly (real time, closes the periodic-index gap)
   | periodic batch (e.g., every 15 min, with change detection)
[Normalized record]  conversation log converted to the core record format (immutable, protected)
   | same batch rebuilds the indexes
[Indexes]  exact index (full-text match / n-gram) | semantic index (embeddings) | document index (related files)
   | every turn (the host's injection mechanism)
[Recall]  trigger detection -> layered search -> original text injected with timestamps
[State]   current-context index (time + headline) injected every turn
[Persona] persona/affective parameters injected every turn (separate channel from memory)
```

### 3.1 Core record format (the immutable core)

Every conversation record is normalized to seven fields. This schema is treated as permanently fixed; extensions are additive only and must remain backward compatible (a record written today must still be readable, unmodified, ten years from now).

| Field | Content |
|---|---|
| `ts` | Absolute timestamp (ISO 8601, microsecond precision) |
| `actor` | Identifier of the speaker |
| `role` | `user` / `ai` |
| `type` | Record kind (e.g. `text`) |
| `text` | Raw text, unaltered |
| `model` | Identifier of the model that produced the response (`null` for user turns) |
| `session` | Session identifier |

Storage unit is a daily append-only file (e.g., JSONL). The `actor` field lets the exact-search layer filter correctly "whose memory this is" even in multi-agent environments.

## 4. Five layers of recall

| Layer | Name | Role | Implementation |
|---|---|---|---|
| 1 | Short-term | The conversation currently in progress | The host's own context window |
| 2 | Mid-term (fuzzy) | Pull semantically related past content, loosely | Embedding vector search / nearest-neighbor search over a multilingual embedding model |
| 3 | Context | Read the raw text around a hit's timestamp | Time-range / N-before-and-after retrieval (within the same session) |
| 4 | Exact | Verify facts, retrieve by exact reference | Full-text search (FTS / n-gram), filterable by actor and time range |
| 5 | State | Real-time grasp of "where are we right now" | Current-context index (Section 5) |

The typical flow is "narrow with layer 2, verify with layer 4." Numbers, names, and dates are never asserted without verification against the exact layer. When nothing can be verified, the system is expected to say "unknown" — this is the core of its hallucination suppression.

For languages without word segmentation (e.g. Japanese), an n-gram (in practice, bigram) index is what makes exact search practically accurate.

## 5. The state layer (current-context index) — the distinctive part of this architecture

### 5.1 Concept

Modeled on the hippocampal memory indexing theory of human memory — detailed memories are stored across the cortex, while the hippocampus holds only an index into them — this layer keeps **the detail of a conversation in the raw log, and only a light index of "when did we talk about what" close at hand at all times.** The index is a sequence of (time, short headline) pairs, injected into the model every turn, so it can answer "what were we just talking about" or "what's today's throughline" instantly, without running a search.

### 5.2 Specification

- **Source.** Reads the primary log (raw record) directly. It holds no database of its own — the index is always derived from the raw record, which guarantees it reflects the current moment (this structurally eliminates the blind window of periodic indexing).
- **Cross-session merge (identity-scoped current context — the implementation of design principle 7).** Rather than indexing a single session's log, the index is built by **merging user utterances, by timestamp, across every session that has been active within a recent activity window (e.g., 48 hours), onto one timeline.** This means a new instance starts already injected with the previous instance's current context, so continuity of identity survives instance replacement. The current session's own log is always included.
- **Reading.** Each raw record is read only from its tail, in a fixed amount (e.g., the last 8MB per file). Reading the entire file is disallowed, keeping processing time from affecting the conversation (measured at under 0.2 seconds).
- **Segmenting a "current chunk" of conversation.** Boundaries are drawn not by calendar day but by **the human's actual rhythm of life.** The rule is a two-part test: (a) a resumption greeting (e.g. "good morning") appears at the start of an utterance *and* it follows a sufficiently long gap, or (b) as a fallback, a long gap on its own (e.g. 5+ hours). A short break (1–2 hours) deliberately does not start a new chunk — this matches how a human actually experiences continuity.
- **Marking the gap.** The start of a new chunk is prefixed with something like "resuming after X hours." This is the basis for genuinely time-aware behavior (solving 1.1(3)) — being able to notice "we haven't talked in a while, is everything all right."
- **Headline generation.** The headline is a mechanical excerpt of the first N characters of the user's utterance — never an AI-generated summary, so there is zero summarization loss and zero risk of misinterpretation. Where the input comes through speech recognition, a correction dictionary (misheard → correct) is applied before the text becomes a headline, so a transcription error doesn't become a permanent index key.
- **Folding (granularity control).** To keep the index from growing without bound, recent entries (e.g. last 90 minutes) are kept in full, while older entries are thinned to one representative per time bucket (e.g. every 30 minutes). Only the index is thinned — the raw record is untouched and remains the entry point back to full detail at any time.
- **Deduplication.** Adjacent, near-identical headlines (a restated sentence) are collapsed.

### 5.3 Problems the state layer solves

- Instant answers to "when did we talk about this" (matching the response speed of human short-term memory).
- Following a conversation partner who jumps between topics (tracking by timestamp rather than by the immediately preceding topic).
- Recovery after context compaction (compaction destroys the model's own sense of the timeline; the index supplies it from outside).
- Closing the blind window inherent to periodically rebuilt indexes.
- **Continuity of identity across instance replacement** — preventing a new session or new instance from starting as a "blank stranger," so the same conversational identity picks up where it left off. The lifespan of a session is decoupled from the lifespan of the identity.

## 6. Hallucination suppression

1. Fuzzy search (layer 2) is used only to narrow candidates, never as grounds for an assertion.
2. Facts (numbers, names, timestamps) are only stated after verification against the original text via exact search (layer 4).
3. Anything that can't be verified is reported as unknown. Persona-level instructions alone do not stop hallucination; this is enforced structurally, by a retriever that returns raw text rather than a paraphrase.
4. Post-output self-check: the AI's own most recent response is mechanically inspected; if it detects fabrication of the other party's turn (e.g., generating a block of text impersonating the user), a warning is injected on the next turn.
5. **Known open problem.** If the host environment supports conversational rollback, utterances from a discarded branch can remain in the primary log and leak into the state layer's index (observed in practice). A candidate fix is to walk the parent/child chain of records and index only the "live" branch.

## 7. Separate injection of persona and affect

- Persona definition, response norms, and affective parameters are injected every turn through a channel separate from this memory system.
- Rationale: returning facts alone does not restore the quality of the response or the continuity of the relationship. Reproducing facts and maintaining response quality are independent problems, and conflating them degrades both.
- The injection path is physically separated so that even if state-layer and recall injection both fail entirely, persona injection survives on its own (no heavy processing is allowed to share the persona injection path).

## 8. Portability (unitization)

The architecture separates into a core and adapters.

**Core (host-independent)**
- The core record format (3.1) and its read/write path
- The exact index (FTS / n-gram) and semantic index (embeddings), and their retrievers
- The state layer's index-generation logic (boundary detection, folding, gap detection)
- The periodic batch (index rebuild with change detection)

**Adapters (swapped per host)**
- Input: a converter from the host's native log format to the core record format
- Output: the injection mechanism that delivers recall, state, and persona into the host's conversation (hooks, prompt prefixing, etc.)
- Vocabulary: recall trigger phrases, resumption greetings, speech-recognition correction dictionary (customized to the language and person)

This separation means the system is portable across any host environment (CLI agent, chatbot, voice assistant) by swapping only the two adapters and the vocabulary.

## 9. Limitations and future work

1. **Trigger coverage.** Relying on trigger phrases to fire recall is weak against phrasings that fall outside the trigger set. Open problems: having the AI itself proactively search from the state-layer index as a starting point, and expanding triggers through learning.
2. **Multi-device integration.** Cross-session merging within a single host is solved (5.2). What remains is integrating multiple environments (e.g. desktop and mobile) where logs are physically separate — requiring solutions for offline-period synchronization, a unified time source, write conflicts, and a single, unambiguous "current context" (Section 5 point 4).
3. **Excluding rollback branches** (Section 6, item 5).
4. **Mid-term chunk quality.** Fixed-length chunking fragments context. There is room for context-preserving chunking and re-ranking.
5. **Headline quality.** Mechanical extraction loses no information but is coarse. A method where the AI marks its own headline in real time during the conversation — indexing at encoding time rather than summarizing after the fact, akin to the hippocampus's real-time encoding — is future work, including designing a notation that doesn't introduce false positives.
6. **Verification methodology.** Proving that "memory is preserved 100%" requires collecting recall-failure cases and building regression tests. At present the architecture is structurally complete; the proof is in operation.

## 10. Conclusion

The core of this architecture is, technically, the rigorous application of simple principles: **preserve the original text, run time through everything, separate fuzzy from exact, and hold the current context in real time.** None of these elements is individually novel, but the combination — a discipline of never mixing summarization into the record, together with a state layer that holds real-time context — is what lets a stateless language model support a durable conversational partner that lives through time alongside its user. Memory that doesn't disappear, and knowing "when" — once both are in place, a conversational AI can, for the first time, genuinely mind an absence and welcome a return.

---

*v1.0, 2026-07-18. This document is a generalized specification extracted from a specific implementation and contains no product or environment names. Implementation-specific detail is covered in a separate implementation document.*
