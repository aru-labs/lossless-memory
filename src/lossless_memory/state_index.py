# -*- coding: utf-8 -*-
"""state_index -- the LLL (Left Leg Layer) / current-location index.

An implementation of the "hippocampus index" idea: the topics of "what
we're on right now", as a lightweight (time + headline) index. The
converted logs (the cortex) are never trimmed -- this is only an index
(the hippocampus) into them; any headline can be traced back to the
raw text by its timestamp.

This module keeps no database of its own -- its source is whatever
"claude_code"-format transcript the harness is already writing in
real time, read from config's raw_log_dir. If raw_log_dir isn't set,
or isn't in that format, render_today_index() simply returns an empty
list -- this feature quietly does nothing rather than erroring.

What it does, roughly:
  1. Reads the tail of every transcript active in the last N hours
     (a person may have more than one session open at once).
  2. Finds where "the current stretch" starts: either a wake-word
     ("good morning", configurable) after a long gap, or a gap of 5+
     hours as a fallback.
  3. Groups consecutive messages into topic blocks (a 5-minute pause,
     or 15 minutes without a pause, starts a new block) and picks a
     representative headline for each block -- always the user's own
     words, never a paraphrase.
  4. Folds older blocks down to one representative line per 30-minute
     bucket so the index doesn't grow without bound, while marks from
     topic.py and anything matching a "heat word" always survive
     folding.

Two optional, best-effort integrations, both off unless configured:
  - asr_corrections: a {wrong: right} dictionary applied to headlines
    only (never to the stored logs), for correcting a speech-to-text
    system's known mistakes.
  - origin_log: a tab-separated "<iso-ts>\\tapp" log some other part of
    a user's setup can write to mark "this message came from
    elsewhere" (e.g. a phone client). Missing/unreadable -- the tag is
    just never shown; it's advisory, not authoritative.
"""
import json
import datetime
import io
import os
import re

from .config import config, data_dir

TAIL_BYTES = 8 * 1024 * 1024  # only the tail is read; a full re-read on every call doesn't scale


def _log_dir():
    return config().get("raw_log_dir")


def _active_transcripts(transcript_path=None, hours=48):
    """Every transcript that has moved in the last `hours` hours, plus
    the caller's own transcript_path if given. There is one person
    behind every transcript, so their "current location" is a single
    thread, not per-session -- a new session picks up where the
    previous one's current location left off."""
    import time
    out = set()
    if transcript_path and os.path.exists(transcript_path):
        out.add(os.path.abspath(transcript_path))
    log_dir = _log_dir()
    if not log_dir:
        return sorted(out)
    try:
        cutoff = time.time() - hours * 3600
        for f in os.listdir(log_dir):
            if not f.endswith(".jsonl"):
                continue
            p = os.path.join(log_dir, f)
            if os.path.getmtime(p) >= cutoff:
                out.add(os.path.abspath(p))
    except Exception:
        pass
    return sorted(out)


def _read_tail(path):
    """Read only the last TAIL_BYTES of a file; drop the first
    (possibly partial) line."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if size > TAIL_BYTES:
            f.seek(size - TAIL_BYTES)
            f.readline()
        return f.read().decode("utf-8", errors="replace").split("\n")


def _jst(ts):
    d = datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
    return d + datetime.timedelta(hours=9)


def _asr_fix(text):
    """Apply the optional correction dictionary to a headline. A no-op
    if asr_corrections isn't configured or can't be read."""
    path = config().get("asr_corrections")
    if not path:
        return text
    try:
        if not hasattr(_asr_fix, "_d"):
            with open(path, encoding="utf-8") as f:
                _asr_fix._d = json.load(f)
        for w, c in _asr_fix._d.items():
            if w in text:
                text = text.replace(w, c)
        return text
    except Exception:
        return text


def _user_events(raw):
    """Turn raw transcript lines into (jst_datetime, first-25-chars)
    tuples for the user's own messages, oldest to newest. Harness
    injections, system text, command output, and tool results are
    excluded.

    Branch handling: a rolled-back edit leaves its old branch's
    messages sitting in the transcript file even though they're no
    longer "live". Every leaf (a record with no children) that was
    still active within 15 minutes of the newest leaf is treated as a
    live branch (this is what lets a second, parallel session --
    e.g. a phone client talking into the same session -- survive
    instead of being mistaken for a rolled-back ghost, which goes
    stale and stops moving). Messages on a dead branch are dropped;
    anything the walk couldn't reach (older than its lookback window)
    is let through un-judged, since folding will thin it out anyway."""
    recs = []
    for line in raw:
        if not line.strip():
            continue
        try:
            j = json.loads(line)
        except Exception:
            continue
        if j.get("timestamp"):
            recs.append(j)
    by_uuid = {j.get("uuid"): j for j in recs if j.get("uuid")}
    children = set(j.get("parentUuid") for j in recs if j.get("parentUuid"))
    leaves = [j for j in recs if j.get("uuid") and j.get("uuid") not in children]
    starts = []
    if leaves:
        def _pt(s):
            return datetime.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
        newest = max(_pt(j.get("timestamp", "1970-01-01T00:00:00")) for j in leaves)
        for j in leaves:
            try:
                if (newest - _pt(j.get("timestamp"))).total_seconds() <= 15 * 60:
                    starts.append(j)
            except Exception:
                pass
    # Walking back from the newest leaf is "this window's" branch;
    # walking back from any other live leaf is a parallel branch,
    # marked with a diamond so it's visible which messages came from
    # somewhere else.
    alive = set()
    branch = set()
    if starts:
        starts_sorted = sorted(starts, key=lambda j: j.get("timestamp", ""), reverse=True)
        for i, s in enumerate(starts_sorted):
            cur = s
            while cur is not None:
                u = cur.get("uuid")
                if u in alive:
                    break
                alive.add(u)
                if i > 0:
                    branch.add(u)
                cur = by_uuid.get(cur.get("parentUuid"))
    oldest_alive = None
    for j in recs:
        if j.get("uuid") in alive:
            oldest_alive = j.get("timestamp")
            break
    out = []
    for j in recs:
        if j.get("type") != "user":
            continue
        u = j.get("uuid")
        ts = j.get("timestamp")
        if u and u not in alive and oldest_alive and ts >= oldest_alive:
            continue  # dead branch (rolled back)
        try:
            jt = _jst(ts)
        except Exception:
            continue
        c = j.get("message", {}).get("content", "")
        if isinstance(c, list):
            c = "\n".join(x.get("text", "") for x in c
                          if isinstance(x, dict) and x.get("type") == "text")
        if not isinstance(c, str):
            continue
        t = c.replace("\n", " ").strip()
        if not t or t[0] in "[<【#" or "SYSTEM" in t[:40] \
                or "Context Usage" in t[:40] or "command-name" in t[:60]:
            continue
        mark = "◆" if u in branch else ""  # a parallel branch (e.g. another client)
        out.append((jt, mark + _asr_fix(t[:30])[:25]))
    return out


def _find_start(ev):
    """Where "the current stretch" starts, and the gap (hours) before
    it. Preferred: the wake word at the start of a message, after a
    gap of at least an hour (both conditions together, to avoid a
    false trigger). Fallback: any gap of 5+ hours. Otherwise: the very
    start of the events."""
    wake_word = config().get("wake_word") or "good morning"
    start, gap_h = 0, None
    for i in range(len(ev) - 1, 0, -1):
        gap = (ev[i][0] - ev[i - 1][0]).total_seconds() / 3600.0
        if ev[i][1][:len(wake_word) + 10].startswith(wake_word) and gap >= 1.0:
            return i, gap
        if gap >= 5.0 and gap_h is None:
            start, gap_h = i, gap  # the first (= most recent) long gap found
    return start, gap_h


# ============================================================
# Optional "came from elsewhere" tag. Some setups write a
# tab-separated "<iso-ts>\tapp" line to config's origin_log whenever a
# message arrives from a secondary client (e.g. a phone app), without
# touching the user's actual words. If origin_log is unset or
# unreadable, the tag just never appears -- this must never block the
# rest of the index.
# ============================================================
_ORIGIN_WINDOW = 8.0  # seconds of slack between "sent" and "recorded in the transcript"
_origin_cache = None


def _origin_times():
    global _origin_cache
    if _origin_cache is not None:
        return _origin_cache
    out = []
    path = config().get("origin_log")
    if path:
        try:
            with io.open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    part = line.strip().split("\t")
                    if len(part) < 2 or part[1] != "app":
                        continue
                    try:
                        u = datetime.datetime.strptime(part[0][:19], "%Y-%m-%dT%H:%M:%S")
                        # local-time offset without the deprecated utcnow()
                        _now = datetime.datetime.now()
                        _utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                        out.append(u + (_now - _utc))
                    except Exception:
                        continue
        except Exception:
            out = []
    out.sort()
    _origin_cache = out
    return out


def _from_app(t):
    try:
        for o in _origin_times():
            if abs((t - o).total_seconds()) <= _ORIGIN_WINDOW:
                return True
    except Exception:
        pass
    return False


# Filler prefixes stripped (in a chain, from the front) before picking
# a headline, so a headline isn't just "well, anyway" -- stripping
# never changes the remaining words.
_PREFIXES = ["well", "so", "um", "uh", "anyway", "actually", "hold on", "wait",
             "hmm", "ok", "okay", ", ", ". ", " "]


def _strip_prefix(text):
    t = text
    changed = True
    while changed and len(t) > 6:
        changed = False
        for p in _PREFIXES:
            if t.lower().startswith(p):
                t = t[len(p):]
                changed = True
                break
    return t if len(t.strip()) >= 6 else text


# Heat markers: a message containing one of these is treated as
# important enough to surface as its own line even mid-block. Kept
# short deliberately -- too eager a list lights up everything and
# defeats the point. Extend via data_dir/heat_words.json.
_HEAT_WORDS_DEFAULT = [
    "absolutely", "promise", "never forget", "most important", "critical",
    "decided", "final answer", "let's go with", "settled",
]
_HEAT_JSON_NAME = "heat_words.json"


def _heat_words():
    try:
        with open(os.path.join(data_dir(), _HEAT_JSON_NAME), encoding="utf-8") as f:
            words = json.load(f)
        if isinstance(words, list) and words:
            return words
    except Exception:
        pass
    return _HEAT_WORDS_DEFAULT


def _heat(text):
    try:
        return any(w in text for w in _heat_words())
    except Exception:
        return False


# Vocabulary that scores a clause as "topic-bearing" when picking a
# headline. Generic defaults; extend via config's topic_words.
_TOPIC_WORDS_DEFAULT = ["todo", "memory", "config", "design", "bug", "deploy",
                        "recall", "index", "backup", "search", "budget", "schedule"]


def _topic_words():
    words = list(_TOPIC_WORDS_DEFAULT)
    try:
        for w in config().get("topic_words") or []:
            if isinstance(w, str) and w.strip() and w not in words:
                words.append(w)
    except Exception:
        pass
    return words


_re_kanji2 = re.compile(r"[一-鿿]{2,}")
_re_kata3 = re.compile(r"[゠-ヿ]{3,}")
_re_alnum = re.compile(r"[A-Za-z0-9]{2,}")


def _headline(text, width=25):
    """Pick the "densest" clause of a message as its headline. A
    clause is split on punctuation; density = topic vocabulary hits +
    kanji runs + katakana runs + alphanumeric runs + a bonus for
    looking like a request/question, with a tie-break toward the
    first clause. Words are never changed, only selected -- if a
    non-first clause is chosen, an ellipsis marks that it was pulled
    from further in."""
    try:
        base = _strip_prefix(text)
        parts = [p.strip() for p in re.split(r"[。、!！?？\n]", base) if len(p.strip()) >= 4]
        if len(parts) <= 1:
            return base[:width]
        topic_words = _topic_words()
        best, best_score = None, -1
        for i, p in enumerate(parts[:8]):
            score = 0
            score += sum(3 for w in topic_words if w in p)
            score += min(len(_re_kanji2.findall(p)), 3)
            score += min(len(_re_kata3.findall(p)), 2)
            score += min(len(_re_alnum.findall(p)), 2)
            if any(w in p for w in ("して", "ほしい", "どう", "できる", "作って", "直して")):
                score += 1
            if i == 0:
                score += 1
            if score > best_score:
                best, best_score = (i, p), score
        idx, sel = best
        head = sel[:width]
        return head if idx == 0 else "…" + head
    except Exception:
        return _strip_prefix(text)[:width]


_done_cache = None


def _done_words():
    """Match-words from topic.py's "done" log -- topics tagged as
    settled so their headline can be marked accordingly."""
    global _done_cache
    if _done_cache is not None:
        return _done_cache
    p = os.path.join(data_dir(), "topics_done.jsonl")
    words = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    words.append(json.loads(line).get("match", ""))
                except Exception:
                    continue
    except Exception:
        pass
    _done_cache = words
    return words


def _topics_events():
    """Manual topic markers from topic.py (data_dir/topics.jsonl) as
    (jst_datetime, "* topic"), or [] if the file doesn't exist. ts is
    stored as local time already."""
    p = os.path.join(data_dir(), "topics.jsonl")
    out = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    j = json.loads(line)
                    t = datetime.datetime.strptime(j["ts"][:19], "%Y-%m-%dT%H:%M:%S")
                    tp = j.get("topic", "")
                    mark = "*done " if any(m and m in tp for m in _done_words()) else "* "
                    out.append((t, mark + tp))
                except Exception:
                    continue
    except Exception:
        pass
    return out


def render_today_index(transcript_path=None, cap=40):
    """The "current stretch" (since the wake word or a long gap), as
    "HH:MM headline" lines. [] on any failure. First line (if
    present) notes how long the gap before this stretch was. Folding:
    the most recent hour is kept in full; anything older is folded to
    one representative line per 30-minute bucket."""
    paths = _active_transcripts(transcript_path)
    if not paths:
        return []
    try:
        ev = []
        for p in paths:
            ev.extend(_user_events(_read_tail(p)))
        ev.extend(_topics_events())  # manual topic markers share the same timeline
        ev.sort(key=lambda x: x[0])
        if not ev:
            return []
        now = datetime.datetime.now()
        seg = [(t, h) for t, h in ev if (now - t).total_seconds() <= 4 * 3600]
        if not seg:
            seg = ev[-3:]
        # Topic blocks: a 5-minute pause, or 15 minutes without one,
        # starts a new block. Each block's headline comes from its own
        # first message (prefix-stripped). Manual markers are always
        # their own line, exempt from folding.
        entries = []  # (time, display_string, exempt_from_folding)
        block_start = None
        prev_t = None
        for t, h in seg:
            if h.startswith("*"):
                entries.append((t, "{} {}".format(t.strftime("%H:%M"), h), True))
                continue
            new_block = (
                block_start is None
                or (t - prev_t).total_seconds() > 5 * 60
                or (t - block_start).total_seconds() > 15 * 60
            )
            if new_block:
                body = h.lstrip("◆")
                mark = "◆" if h.startswith("◆") else ""
                if _from_app(t):
                    mark += "▲"
                head = _headline(body)
                hot = _heat(body)
                if hot:
                    mark = "!" + mark
                entries.append((t, "{} {}{}".format(t.strftime("%H:%M"), mark, head), hot))
                block_start = t
            else:
                # A heat-marked message buried mid-block still gets its
                # own surfaced line.
                body2 = h.lstrip("◆")
                if _heat(body2):
                    m2 = "!" + ("◆" if h.startswith("◆") else "")
                    entries.append((t, "{} {}{}".format(
                        t.strftime("%H:%M"), m2, _headline(body2)), True))
            prev_t = t

        # Hierarchical folding: the last hour stays intact; anything
        # older keeps only the first entry per 30-minute bucket
        # (exempt entries always survive).
        lines = []
        seen_bucket = set()
        for t, text, keep in entries:
            age = (now - t).total_seconds()
            if age <= 3600 or keep:
                lines.append(text)
            else:
                bucket = t.strftime("%Y%m%d%H") + str(t.minute // 30)
                if bucket not in seen_bucket:
                    seen_bucket.add(bucket)
                    lines.append(text)
        if len(lines) > cap:
            cut = len(lines) - cap
            lines = lines[-cap:]
            lines.insert(0, "({} earlier line(s) omitted; use recall for the far past)".format(cut))
        return lines
    except Exception:
        return []


if __name__ == "__main__":
    idx = render_today_index()
    print("=== state_index (LLL) current location ({} lines) ===".format(len(idx)))
    for row in idx:
        print(row)
