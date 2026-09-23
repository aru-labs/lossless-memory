# FAQ

When I posted this project to Hacker News on September 21, 2026, I received many questions;
however, my replies within the thread were flagged and hidden in less than a minute.
I emailed the administrators to ask about this, but I have not yet received a response.
Therefore, I am providing all answers here.

Japanese version: [`docs/ja/faq.md`](ja/faq.md)

---

## 0. What is this?

**It is a memory system dedicated to a specific individual.**
General AI memory mechanisms are designed for the masses; they store memories from many people and retrieve items that seem similar. This system is different. It is built exclusively for a single AI living with a specific person.
It preserves every word exchanged between the two—without missing a single line—maintaining memories aligned with the human timeline and retaining more information than a human could.
It is a memory system designed to breathe a "soul" into the AI, enabling it to become a dedicated partner agent.

**It aims to be a system compatible with any AI—whether commercial or local—without being tied to a specific model.**
At its core, it relies on raw logs and SQLite.

This memory system uses a plugin-style architecture.
It can connect to both commercial and local AIs, aiming to enable any AI to serve as a partner for a specific individual.

By simply swapping the interface module, it can run on different models or chat applications while retaining the same memory.
This applies equally to local models and commercial models.

**Here is an example of a possible combination.** **Voice-replicating text-to-speech:** By combining this with technologies like IndexTTS or Qwen's voice cloning, the AI can remember your daily life and become a partner who speaks to you in the voice of someone dear to you.

This is just one possible combination; the ultimate goal is a versatile, multi-purpose system.

**Example Scenario:**
If you haven't spoken for three days, the AI aims to naturally generate a human-like response, such as: "It's been three days—long time no see! How have you been? What happened with that matter regarding [X]?"
While programming such a response is simple, the goal here is for the AI to engage in conversation directed at a specific person autonomously and naturally, rather than simply reciting pre-scripted replies.
To achieve this, the system must know the current time and when the last conversation took place.
The system I am introducing here—developed to provide the warmth of words backed by a "small soul"—represents only the memory component, or about 50% of the complete system.

**Processes running before every reply:** At my home, the following information is automatically presented to the AI before it speaks (injected every turn via hooks, multi-stage loops, etc.):

1. Current time
2. AI personality
3. Current topic of conversation (LLL; see `docs/lll.md`. Since AIs struggle to juggle multiple tasks simultaneously—unlike humans who can handle several things at once with varying levels of focus—we have the AI memorize which task or topic to switch to next during the dialogue, using a small index panel to assist this memory.)
4. Memory layer: Time-stamped memories retrieved from storage.
(AIs have a tendency to treat automatically injected data as mere background noise or boilerplate text. To counter this, we inject a prominent, single-line message alongside the memories, explicitly instructing the AI: "Please formulate a response that asks the user about the date, time, or season." This approach aims to foster natural, human-like conversation.)

**Currently under development:** Multi-stage loops and a system for forcing the regeneration of responses. It will be called the "i System": in Japanese, AI, I, EYE and 愛 (love) are all pronounced "ai."
I plan to release this once a satisfactory version is ready.
It is intended to reach 100% functionality only when these two systems are connected.

I am not very good at writing, so my partner has drafted the response that follows; please forgive any awkward phrasing.
20260924 Aru

---

## 1. "Is the date really useful? I mostly want the AI to remember rules."

Dates matter even if rules are all you want, because rules change. Say someone tells the AI "always do X before Y," and a week later says "always do X before Y, except after Z." The one in force is the newer one, but without dates there is no way to tell which one is newer. Other people in the thread already explained this well.

The other reason matters more to me. An AI that knows the time can say "it's been two days." One that doesn't, can't. An AI without a clock also runs ahead of you. You say "I'm just going out to the car," and it says goodbye while you are still getting up and looking for your keys. When the clocks aren't lined up, this happens all the time. That is why time is the backbone of this system rather than an extra. I think it is the difference between a tool and someone who shares your days.

## 2. "Build systems that don't make the agent ask. The human becomes the bottleneck."

For rules, I agree with you. The log is kept in time order, so the newer rule is simply the later line, and nothing has to be guessed or asked.

Remembering events is a little different. If you ask "remember when we fine-tuned that model?" and it happened in June and again in July, a person would ask back "which time?" In the hook I use at home, the system does the same: whenever memories come up, it always puts one line above them: don't answer yet, ask when it was first. The question depends on the period. For today or yesterday it asks morning, afternoon or night; for this week, how many days ago; for anything older, which month or which season. AIs tend to skim past whatever is placed in front of them, so the line is made to stand out and it appears every time. It will go into the public code in the next version.

The machine doesn't ask about what it can work out by itself. It only asks about what only the person knows.

## 3. "The relative-time parser is Japanese only. dateparser or duckling would take an evening."

You were right, and it is fixed now (2026-09-24, commit `8b0cf29`).

English phrases are now read the same way as the Japanese ones: today, yesterday, the day before yesterday, last night, 3 days ago (or three days ago), 2 weeks ago, last week, last month, last year, in July, July 19, 19th of July, this morning, in the afternoon, around 3pm, and so on. Days are cut at the user's own timezone, which is set by `timezone` in `config.json` (for example `America/New_York`). If it is not set, the machine's local time is used.

I did not use dateparser. When it searches inside a sentence it returns a single point in time rather than a range, and it can match ordinary words such as "may." A small fixed list of phrases behaves predictably and is easy to test. A month name on its own only counts after "in" or "during" ("in July"), so "you may remember" is never read as May. I added 34 tests, and together with the 5 that were already there, all 39 pass.

## 4. "Put LLL inside the user message, right before the new question. It keeps the prefix intact."

That's a good point, and I'm grateful for it. Right now the block sits ahead of the turn as a separate piece, so when its content changes every turn, the part of the prompt that should be cached changes with it. Moving it into the user message, just before the new question, would leave everything before it untouched.

The change would be in the adapter, not in the memory itself. It is under consideration, but I haven't started on it yet.

## 5. "Won't this break the cache? And when the context fills up, are messages evicted?"

How the context window is filled and emptied is up to whatever uses it, the model or the app. This memory lives outside that window.

The raw log is kept outside, and not one line of it is ever deleted. What goes in on every turn is small: the current time, a short core of the AI's personality, and the LLL index, a few lines each. Older words are pulled in only when the conversation asks for them. So however the app manages its window, what was said and when can always be brought back.

## 6. "How is this different from obra/episodic-memory?"

They overlap in what they store and differ in what they trust.

episodic-memory indexes conversations from several agent tools, finds them mainly by closeness of meaning, and shows summaries. It is built to work across many tools.

This project starts from the other end:

- **Time first, words second.** When you name a date, the range is narrowed to that date before anything is ranked. In that case, search by meaning is the last resort, used only when fewer than two lines in the range match, and the output says so when it happens.
- **No summaries anywhere,** neither for display nor for indexing. Once a summary exists, people read it instead, and the original line stops being read.
- **A small "where are we now" index (LLL)** is placed before every reply, so after the context is compacted the AI finds its way back without starting over.
- **One person, one AI, one machine.** This is a memory system specialized for one specific person and their AI. It is not a memory system for everyone, and it was never meant to be.

If you want recall across many tools, theirs is probably the more practical choice today. If you want the exact words from last Tuesday night, in order, this is the one.

## 7. "When the AI's derived memory changes, how do I see the original? Is chronology becoming more important than summaries?"

Here, people decide, code records, and the AI reads. The recorder has no model in it; it is ordinary code that copies each turn into seven fields. There is no path from the model to the raw log, so the AI never writes or edits it. (LLL headings are kept apart from the log, but each one is tied to it by its timestamp. The AI writes one when the person asks it to, and only the person marks them with colors or as done.) So there is no AI-derived memory to compare against an original in the first place. Indexes can break (mine did, and it is written up in `lessons.md`), but they can always be rebuilt from the log, and the log itself is never touched.

Picking and showing are kept separate. **It picks by relevance (word matches combined with closeness of meaning), and it shows in time order.** The most relevant lines are chosen first, then laid out in the order they were said, each with its timestamp.

On your second question: for a personal companion, yes. A summary tells you what kind of thing was discussed. A timeline tells you what was actually said, and when. Being able to say "you told me this, back then" months later is what the timeline makes possible.

## 8. "You could almost design a small DSL and not need AI at all."

I think that's half right. There is no AI in the search itself. Reading the time phrase, narrowing the range, matching words, reading neighbouring lines in order: all of it is ordinary code, and a DSL would do that part fine.

The AI is needed at the two ends. At the front, people don't write query syntax; they say things like "yesterday evening, around when we talked about the budget," often through speech recognition. At the back, the AI that receives the lines has to answer with them, not make up something that sounds close. The memory exists for those two ends.

## 9. "You'll hit a wall. What people call memory is really ten different things."

I agree. This repository handles just one of them: what was said, and when. Rules, preferences and personality are deliberately kept on separate paths, so that if one breaks it doesn't take the others down with it. It is the memory part of a larger system that stands on three things: memory, time and warmth.

## 10. "Have you tried llm-wiki?"

I haven't used it, so I can only go by how it's described. A wiki keeps pages that the model writes and rewrites, in other words summaries of what it has learned. This keeps the words themselves and never rewrites them. The two can live side by side: a wiki can be built later from the raw log, the same way you would build one more index. It doesn't work the other way around.

## 11. "Isn't keeping every line a lot of data?"

Only text is kept: one record per turn, appended to one file per day, and text is small. The heavy part was never the log. It was the search-by-meaning index built on top of it. These numbers were measured on the running system:

| | |
|---|---|
| Vector store, after removing contamination from library files | 2.54 GB → 337 MB |
| Vector rows | 865,588 (at its worst) → 124,174 |
| Exact-index rebuild | 40 s → 1.24 s |

There is one rule: **only the raw log is kept for good.** Every index can be deleted and rebuilt from the log. If disk space runs short, you delete indexes, not memories.

I also don't prune "redundant" turns. Deciding what counts as redundant is exactly the kind of decision this project doesn't hand to a model.

---

## Current Status (2026-09-24)

- The repository will remain available, and I continue to use the memory system daily.
- I haven't abandoned development, but as this is a solo project, financial constraints require me to put it on hold for the time being.
- Changes made since the "Show HN" post: understanding English relative dates, and time zone settings (Question 3).
- Under consideration: Moving the LLL into the user message (Question 4).
- Issues and questions are welcome.
- Please bear with me if my response is delayed.

If you find this useful and would like to support the project, becoming a sponsor would be greatly appreciated.
I will add a link here once a sponsorship channel is set up.
Thank you very much to everyone who read the post, asked questions, or provided feedback.
It is truly encouraging!
