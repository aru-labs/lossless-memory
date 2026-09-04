# Temporal Backbone

Every record in the system carries an absolute timestamp, and recall works by scoping a query to a time range before searching within it — "when" is treated as a first-class search axis, not an afterthought bolted onto semantic search.

## Why time first

Plain semantic (vector) search answers "what is this about." It does not answer "when did this happen," and pure semantic distance tends to blur together things that are topically similar but months apart. Human recall doesn't usually work by topic similarity alone either — a person asked "what did we decide last night" already knows the time window and just needs the content inside it. This system is built around the same shape of query: **name a time, then search inside it**, rather than searching everything and hoping time falls out of the ranking.

Concretely, this means the single retrieval entry point accepts queries of the form **"time + words."** Naming a time scopes the search to that range and nothing else is mixed in; naming only a time with no words returns everything in that range, laid out chronologically, so a query can also be used to simply look back over a period rather than to find a specific fact.

## A vocabulary of time an AI can actually parse

For "time + words" to work as an interface, the system has to recognize a reasonably natural range of ways people actually refer to time, not just ISO dates. The recognized vocabulary includes:

- Relative days: today, yesterday, the day before yesterday, "N days ago"
- Relative larger spans: last week, last month, "N months ago" (correctly crossing year boundaries)
- Absolute dates, in more than one common written form
- Parts of day: morning, midday, evening, night, late night
- Specific clock times and rough times ("around 3pm")

Recognizing a month-only expression ("in July") as a date *range* — rather than falling through to being treated as a plain search keyword — turned out to matter in practice; an earlier version of the parser only recognized full dates and silently treated a bare month name as ordinary search text, so a lot of otherwise-scoped queries were quietly running unscoped. See `docs/lessons.md` for more on this class of failure.

## Layered fallback when the time window comes up empty

A time-scoped query does not simply fail silently when nothing matches. It degrades through three layers, and says out loud which layer produced the answer, rather than pretending the top layer always succeeds:

1. **Exact match inside the time window.** Time range plus keyword match, both satisfied.
2. **Time window, keywords relaxed.** If there are too few exact hits, fall back to returning everything in the time range in chronological order, and say so ("few exact matches — showing the whole range in order").
3. **No time window, semantic fallback.** If the time range genuinely has nothing relevant, drop the time constraint and fall back to semantic search across all history, and say so as well ("nothing in that time range — pulled the closest match from the full history").

Results returned with lower semantic confidence are also visibly flagged as low-confidence rather than presented with the same certainty as a verified exact hit. The goal in all of this is the same one that runs through the rest of the memory system: never present a guess as a fact.

## Measured results

These numbers come from real operation of a single-user deployment, not a synthetic benchmark:

- A "time + keyword" recall query resolves in about **0.4 seconds**.
- Rebuilding the exact-match index from scratch on every pass was replaced with incremental appends (plus rewrite detection): **40 seconds → 1.24 seconds** per indexing pass.
- The recall path was hardened through repeated adversarial review passes. The final pass confirmed all 8 previously-found holes closed, and ran 9 regression checks plus 10 novel breakage attempts (huge inputs, malformed dates, a stopped indexer, digit overflows) with zero exceptions — against an internal standard of "one remaining hole means fail."

## What this deliberately doesn't try to do

The temporal backbone is a scoping mechanism, not a full temporal-reasoning engine. It does not resolve genuinely ambiguous references ("that time" with no other anchor) on its own — those fall through to the semantic-fallback layer above, or the system asks which occasion is meant rather than guessing. Its job is narrow and specific: given a time expression in ordinary language, produce a correct time range, and use that range to keep search honest.
