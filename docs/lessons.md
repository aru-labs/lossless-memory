# Lessons Learned

A record of real failures encountered running this system, what caused them, and how each was actually fixed — kept here rather than smoothed over, in the same spirit as the rest of the system: the raw record, not a flattering summary of it.

## The index doubled in size and started returning a foreign dictionary

**Symptom.** The semantic (embedding) index grew from 447,013 lines (measured 2026-08-31) to **865,588** lines (measured 2026-09-04, before the fix) — nearly double, and far more than the actual source material accounted for once the true count was checked. When both the time-scoped search and the exact-text search came up empty and the system fell back to its last resort (semantic search across all history), the results that surfaced were nonsense: entries from a third-party library's foreign-language dictionary file and license text that had never been part of any real conversation or document.

**Root cause.** One of the ingestion steps rewrote its output file completely on every single run (not append-only). The incremental indexer downstream, however, only knew how to *add* rows for lines beyond the last count it had seen — it had no way to detect that a source file had been fully rewritten from scratch, and no way to notice that a source file had disappeared entirely. Over repeated runs, stale rows from earlier versions of rewritten files simply accumulated forever rather than being replaced, and a one-time inclusion of unrelated files from a library's installed package data — never cleaned up — sat in the index untouched, quietly growing whenever anything nearby was reprocessed. Roughly **750,094** lines of that stray, unrelated material were sitting in an index of otherwise real content.

**Fix.** Give the indexer a way to detect "this source file was rewritten" instead of only "this source file grew," using three independent checks per file on every pass: (1) the index contains a line number beyond the file's current line count, (2) the index contains the same line number twice, (3) a content fingerprint of the already-indexed lines no longer matches the current file. If any of the three trips, that file's rows are dropped and re-indexed from scratch; if a previously-seen file is gone entirely, its rows are removed too. A full from-scratch rebuild is no longer required to recover from this class of drift — the same lightweight incremental pass detects and repairs it.

**Measured result (2026-09-04 14:41).** Semantic index: 865,588 → **124,174** lines, matching the actual source material. Stray third-party lines: **0**. Duplicate line numbers: **0**. Stale line numbers beyond current file length: **0**. Index file size: 2.54 GB → 337 MB after compaction.

**The retrospective lesson.** The first attempt at fixing this failed to actually shrink the index, because the design was written from a remembered or documented line count rather than by querying the live database for what was actually in it at the time — the real value had already drifted from what the design assumed. The generalizable rule: **when designing a fix for something that lives in storage — a database, a config file, an index — pull the actual current value from the live store before writing the design.** Reading the code and the documentation is not the same as reading the data.

## A silent argument-parsing default hid a working feature

**Symptom.** A recall option meant to retrieve "N compactions ago" returned the same result regardless of what N was actually supplied.

**Root cause.** The argument parser only recognized one written form of the flag (`--flag=N`) and silently fell back to a default value of 1 for the other common form (`--flag N`, space-separated), because it never checked whether the next token was a bare number. The underlying feature was correct; the option to select a different N was simply never reaching it.

**Fix.** Recognize both forms explicitly. **Lesson**: an option that "does nothing" is not always a broken feature — check whether the argument is actually arriving before debugging the logic downstream of it.

## A confidence flag got silently dropped during result merging

**Symptom.** Results that should have been marked low-confidence (from a fuzzy/semantic match with a middling similarity score) sometimes showed up with no confidence flag at all, as if they were as reliable as an exact match.

**Root cause.** When merging hits from two different search methods keyed by the same identifier, the merge used "first write wins" — if the exact-match result reached a given key first, the semantic-match result's confidence score for that same key was discarded rather than merged in, even though it was the one carrying the information needed to decide whether to flag the result.

**Fix.** Only skip a later write if the earlier one already has the field populated; otherwise merge in the missing field rather than discarding it. **Lesson**: a merge-by-first-write-wins pattern silently drops information whenever the fields that matter aren't all present on the first writer — worth checking explicitly, not assuming "first is good enough."

## The measurement tool that claimed a feature had a 0% success rate

**Symptom.** An evaluation script built to measure how often recall triggers correctly fired reported the exact same result — 0% — before and after a real fix, making it look like the fix had done nothing.

**Root cause.** The evaluation script itself referenced internal names that had been renamed in an earlier refactor. Every call into the renamed code path raised an error internally, which was silently caught and swallowed, producing a default "nothing fired" result every time — a bug in the measurement tool, not in the thing being measured.

**Fix.** Point the evaluation script at the current, single source of truth for that configuration rather than an outdated internal reference. **Lesson**: before trusting a before/after comparison, verify the measurement tool itself still actually calls the thing it claims to be measuring — a broken evaluator that fails silently is indistinguishable from a real negative result until you check.

## A migrated feature list left the old hardcoded version in place

**Symptom.** Removing a trigger phrase from an externalized, editable configuration file did not stop that phrase from firing the older behavior.

**Root cause.** The trigger-word list had been migrated from being hardcoded in source to being read from an external config file, so that it could be edited without a code change. The migration added the config file but never removed the old hardcoded default list it was meant to replace, and the loading logic combined both (config OR hardcoded default) rather than the config alone. As a result, removing an entry from the config had no visible effect, because the hardcoded copy of that same entry was still silently included.

**Fix.** The immediate workaround was to explicitly exclude the phrase in the config's exclusion list, which does take effect; the underlying hardcoded default was left as a known, reported gap rather than modified, since fixing it was outside the scope of the change at the time. **Lesson**: when a hardcoded value is migrated to an external config, the migration is not complete until the hardcoded value is actually removed — leaving both in place with an OR (rather than an override) creates a config that appears to do nothing when edited.

## A device-selection message lied about what hardware was actually used

**Symptom.** A log message printed at the start of a batch embedding run always reported the same fixed device name, regardless of what hardware was actually being used for that run.

**Root cause.** The message was a hardcoded string written once, before the code was later changed to support running on different accelerators — nobody had gone back and made the message reflect the actual runtime choice.

**Fix.** Read the actual device selected at runtime and print that. **Related operational lesson**: when selecting an accelerator by numeric index in a multi-device environment, an off-by-one or misremembered index can silently select the wrong physical device rather than failing loudly — always confirm the device actually acquired from the first line of output, rather than trusting that the requested index and the acquired device are the same thing.

## Records from the wrong "kind" polluted a time-ordered view

**Symptom.** Querying "what happened around this specific time" sometimes returned document/reference material interleaved with actual conversation turns from that time, even though the two are conceptually different things.

**Root cause.** Reference material ingested into the same store used its file's last-modified time as its timestamp field, which is a legitimate timestamp but represents a different kind of event (a document being updated) than a conversational turn happening. Because the time-based query treated all record kinds identically, editing a reference document at a given moment made it appear next to actual conversation from that same moment.

**Fix.** Exclude that record kind specifically from time-ordered conversational queries, while leaving it fully searchable by content. **Lesson**: a shared timestamp field does not imply two records are commensurable — what generated the timestamp matters, and a time-range query needs to know which record kinds actually belong in a "what happened at this moment" view.

## A whole class of date expressions fell through into plain text search

**Symptom.** A query naming only a month, with no day (e.g. "in July"), was not recognized as a date range at all — it was instead treated as an ordinary search keyword, silently losing the time-scoping the query was clearly asking for.

**Root cause.** The date-expression parser had explicit handling for several date shapes but no case for a bare month-name-only expression, so it fell through every pattern match and reached the default "not a date, treat as a keyword" branch.

**Fix.** Add explicit handling for the month-only form, defaulting to the full calendar month as the range (rolling back a year if the month is later than the current one). **Lesson**: partial coverage of a set of natural-language patterns fails silently rather than loudly — an unrecognized time expression doesn't error, it just quietly stops being treated as time at all, which is easy to miss without deliberately testing the gaps between the patterns that are covered.
