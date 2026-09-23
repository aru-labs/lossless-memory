# -*- coding: utf-8 -*-
"""recall -- the one entry point for pulling memory back out by hand.

    python -m lossless_memory.recall "query"

This is the only door: it fuses index_exact (exact match) and
index_vector (semantic match) results, removes duplicates, and prints
them in chronological order with a timestamp on every line -- never
summarized. Don't call index_exact / index_vector separately; call
this.

The strongest way to query is "date + word(s)": naming a date (an
explicit YYYY-MM-DD, or a relative phrase in Japanese or English such
as "yesterday" or "3 days ago") switches to time-scoped mode and
returns only what's in that range, without mixing in semantic matches
from elsewhere.

The output always starts with [NOW] -- knowing the current time first
keeps you from drowning in a wave of search results whose own
timestamps you haven't oriented yourself against yet.
"""
import sys
import os
import io
import re
import json
import glob
import datetime

from .config import config, data_dir, user_tz
from . import index_exact
# index_vector pulls in sentence-transformers, which is heavy and not
# needed for exact-only use -- imported lazily at each call site below
# instead of at module load, so recall still works (exact search only)
# in an environment that never installed it.

try:
    from . import query_rules as _qr
except Exception:
    _qr = None

try:
    from .ingest import META_HEADS, META_MARKS
except Exception:
    META_HEADS = ("This session is being continued",)
    META_MARKS = ("<command-name>", "<local-command-stdout>", "<task-notification>")


def _silence():
    """Swallow whatever a model-loading library prints to stdout on
    import, so it doesn't clutter the search output."""
    real = sys.stdout
    sys.stdout = io.StringIO()
    return real


# Failures are recorded here instead of being indistinguishable from a
# genuine "nothing found" -- a broken search and an empty result must
# never look the same.
_ERR_LOG = os.path.join(data_dir(), "recall_errors.log")
LAST_ERRORS = []
LAST_MODE = []       # which path served this query: "time" / "L3" (time -> semantic fallback) / "exact-only" / []
LAST_TIME_TOTAL = []
LAST_TIME_META = []
LAST_TAIL = []


def _note_error(part, ex):
    msg = "[%s] %s: %s %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"),
                              part, type(ex).__name__, str(ex)[:120])
    LAST_ERRORS.append(msg)
    try:
        with open(_ERR_LOG, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


# Optional query-rewriting: a growable {word: [alternatives]} dictionary
# (data_dir/query_synonyms.json) can supply up to _MAX_VARIANTS reworded
# copies of a query to also search, on top of the original. Empty/absent
# by default.
_MAX_VARIANTS = 2


def _synonyms_path():
    return os.path.join(data_dir(), "query_synonyms.json")


def _expand_query(query):
    try:
        with open(_synonyms_path(), encoding="utf-8") as f:
            syn = json.load(f)
    except Exception:
        return []
    out = []
    for word, alts in syn.items():
        if word.startswith("_") or word not in query:
            continue
        for alt in alts:
            if alt in query:
                continue
            v = query.replace(word, alt)
            if v != query and v not in out:
                out.append(v)
            if len(out) >= _MAX_VARIANTS:
                return out
    return out


def _ts_jst(ts):
    """Render a stored UTC ts as a "YYYY-MM-DD HH:MM" string in the user's timezone."""
    s = str(ts or "")
    try:
        t = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=datetime.timezone.utc)
        return t.astimezone(user_tz()).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return s[:16].replace("T", " ")


def _dedupe_rows(rows):
    """Collapse the same message into one row (ts+actor+text+type as
    the key, so a text row and an action row at the same timestamp
    stay separate)."""
    seen, out = set(), []
    for r in rows or []:
        key = ((r.get("ts") or ""), (r.get("actor") or ""), (r.get("text") or ""),
               (r.get("type") or "text"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


_cfg0 = config()
ACTOR_USER = _cfg0.get("user_name") or "user"
ACTOR_AI = _cfg0.get("ai_name") or "assistant"


def _split_time(query):
    try:
        return index_exact._split_time_query(query)
    except Exception:
        return (None, None, [])


def _vector_search(query, limit):
    """Lazily import index_vector (sentence-transformers is heavy and
    optional) and run a semantic search. [] if the dependency isn't
    installed or the search itself fails."""
    try:
        from . import index_vector
    except Exception as ex:
        _note_error("index_vector(import)", ex)
        return []
    try:
        return list(index_vector.search(query, limit=limit) or [])
    except Exception as ex:
        _note_error("index_vector", ex)
        return []


def fetch(query, log_limit=4, recency=True, tail=True, actor=None, around=0, exact_only=False):
    """The shared core of recall. Returns a deduped list of raw-row
    dicts. Not meant to be called directly by most users -- see
    recall() below, which formats the result for display."""
    del LAST_ERRORS[:]
    del LAST_MODE[:]
    del LAST_TIME_TOTAL[:]
    del LAST_TIME_META[:]
    del LAST_TAIL[:]
    ses_hits, vec_hits = [], []
    real = _silence()
    try:
        _dr, _tr, _kws = _split_time(query)
        _time_mode = _dr is not None
        if exact_only:
            LAST_MODE.append("exact-only")
        if _time_mode:
            # Time-scoped mode: index_exact only. Semantic search is
            # not mixed in here so a date range stays a hard boundary.
            try:
                _hits = max(log_limit, 8) if _kws else 2
                ses_hits, _meta = index_exact.search_time(
                    _dr, _tr, _kws, actor=actor, hits=_hits, around=int(around))
                ses_hits = list(ses_hits or [])
                LAST_MODE.append("time")
                LAST_TIME_TOTAL.append(int(_meta.get("total", 0)))
                LAST_TIME_META.append(_meta)
                # If the date range turned up almost nothing, fall back
                # to a semantic search across all time rather than
                # concluding "nothing" too quickly.
                if len(ses_hits) < 2 and not exact_only:
                    _vec_l3 = _vector_search(query, max(log_limit, 8))
                    if _vec_l3:
                        ses_hits = _dedupe_rows(list(ses_hits) + _vec_l3)
                        LAST_MODE.append("L3")
            except Exception as ex:
                _note_error("index_exact(time)", ex)
                _time_mode = False
        if not _time_mode:
            try:
                if around:
                    ses_hits = list(index_exact.search_with_context(
                        query, actor=actor, hits=max(log_limit, 4), around=int(around)) or [])
                else:
                    ses_hits = list(index_exact.search(query, actor, limit=max(log_limit, 8)) or [])
            except Exception as ex:
                _note_error("index_exact", ex)
            if not actor and not exact_only:
                vec_hits = _vector_search(query, max(log_limit, 8))
                _ds0 = [r.get("distance") for r in vec_hits if r.get("distance") is not None]
                if (not _ds0) or min(_ds0) > VEC_FAR_CUT_NORMAL:
                    for q in _expand_query(query):
                        try:
                            ses_hits += list(index_exact.search(q, actor, limit=max(log_limit, 8)) or [])
                        except Exception as ex:
                            _note_error("index_exact", ex)
                        vec_hits += _vector_search(q, max(log_limit, 8))
    finally:
        sys.stdout = real

    if _time_mode:
        uniq = _dedupe_rows(ses_hits)
    elif actor or exact_only:
        uniq = _dedupe_rows(ses_hits)[: log_limit * 2]
    else:
        uniq = _fuse(ses_hits, vec_hits, log_limit * 2, recency=recency, _fuse_query=query)
    if tail and not _time_mode and not actor:
        exclude = {((r.get("ts") or "")[:16], (r.get("text") or "")[:40]) for r in uniq}
        _tail_search(query, exclude)
    return uniq


# RRF fusion with recency weighting and a staged relevance cutoff.
RRF_K = 60
RRF_REL_CUT = 0.35
# Two cutoffs instead of one hard accept/reject line: 0.51-0.60 is a
# "maybe" band that's kept but flagged [maybe], not dropped outright.
VEC_FAR_CUT_NORMAL = 0.51
VEC_FAR_CUT_CONFIRM = 0.60
ECHO_EXCLUDE_MIN = 15


def _is_echo(query, text):
    try:
        q = re.sub(r"\s", "", query)
        t = re.sub(r"\s", "", text or "")
        if len(q) < 20:
            return q in t and len(q) >= 10
        step = 10
        for i in range(0, len(q) - 20 + 1, step):
            if q[i:i + 20] in t:
                return True
        if q[-20:] in t:
            return True
        return False
    except Exception:
        return False


RECENCY_HALF_LIFE_DAYS = 30.0


def _recency_factor(ts):
    try:
        d = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        age_days = max(0.0, (datetime.datetime.now(d.tzinfo) - d).total_seconds() / 86400)
        decay = 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)
        return 0.5 + 0.5 * decay
    except Exception:
        return 1.0


def _heat_hit(text):
    try:
        from . import state_index
        return state_index._heat(text or "")
    except Exception:
        return False


def _too_recent(ts):
    try:
        d = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        now = datetime.datetime.now(d.tzinfo)
        return (now - d).total_seconds() < ECHO_EXCLUDE_MIN * 60
    except Exception:
        return False


def _recent_48h(ts):
    try:
        d = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        now = datetime.datetime.now(d.tzinfo)
        return (now - d).total_seconds() < 48 * 3600
    except Exception:
        return False


def _fuse(ses_hits, vec_hits, limit, recency=True, _fuse_query=""):
    """Reciprocal-rank fusion of the exact and semantic hit lists, plus
    a recency weighting and the two-stage distance cutoff. Returns the
    fused list, highest score first, deduplicated."""
    _ds = [r.get("distance") for r in vec_hits if r.get("distance") is not None]
    pre_best_d = min(_ds) if _ds else None

    def _drop(r):
        if _too_recent(r.get("ts")):
            return True
        if _is_echo(_fuse_query, r.get("text")) and _recent_48h(r.get("ts")):
            return True
        return False
    ses_hits = [r for r in ses_hits if not _drop(r)]
    vec_hits = [r for r in vec_hits if not _drop(r)]

    def key(r):
        return (r.get("ts"), (r.get("text") or "")[:40])
    scores, items = {}, {}
    for rank, r in enumerate(ses_hits, start=1):
        k = key(r)
        scores[k] = scores.get(k, 0.0) + 1.0 / (RRF_K + rank)
        items.setdefault(k, r)
    for rank, r in enumerate(vec_hits, start=1):
        k = key(r)
        scores[k] = scores.get(k, 0.0) + 1.0 / (RRF_K + rank)
        if k in items:
            if items[k].get("distance") is None and r.get("distance") is not None:
                items[k]["distance"] = r.get("distance")
        else:
            items[k] = r
    if not scores:
        return []
    if pre_best_d is not None and pre_best_d > VEC_FAR_CUT_CONFIRM:
        return []
    for k in scores:
        d = items[k].get("distance")
        if d is not None and VEC_FAR_CUT_NORMAL < d <= VEC_FAR_CUT_CONFIRM:
            items[k]["confirm"] = True
    if recency:
        for k in scores:
            f = _recency_factor(items[k].get("ts"))
            if f < 0.9 and _heat_hit(items[k].get("text")):
                f = 0.9
            scores[k] *= f
    ordered = sorted(scores.items(), key=lambda kv: -kv[1])
    top = ordered[0][1]
    out = []
    for k, s in ordered:
        if s < top * RRF_REL_CUT:
            break
        out.append(items[k])
        if len(out) >= limit:
            break
    return out


_TAIL_BYTES = 512 * 1024


def _is_meta_text(text):
    text = text or ""
    if any(text.startswith(h) for h in META_HEADS):
        return True
    if any(m in text for m in META_MARKS):
        return True
    return False


def _tail_search(query, exclude_keys, limit=3):
    """Best-effort: catch a message so recent the index hasn't picked
    it up yet, by scanning the tail of the raw source files directly.
    Only runs for the "claude_code" ingest format, since it parses
    that transcript shape specifically; a no-op otherwise."""
    del LAST_TAIL[:]
    cfg = config()
    if cfg.get("ingest_format") != "claude_code" or not cfg.get("raw_log_dir"):
        return
    raw_dir = cfg["raw_log_dir"]
    try:
        words = [w for w in re.findall(
            r"[一-鿿]{2,}|[ぁ-ゖ]{3,}|[゠-ヿ]{2,}|[A-Za-z0-9]{2,}", query)][:12]
        if not words:
            return
        files = sorted(glob.glob(os.path.join(raw_dir, "*.jsonl")),
                       key=os.path.getmtime, reverse=True)[:2]
        hits = []
        for fp in files:
            with open(fp, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - _TAIL_BYTES))
                blob = f.read().decode("utf-8", errors="ignore")
            for line in blob.splitlines():
                if '"type"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                typ = o.get("type")
                if typ not in ("user", "assistant"):
                    continue
                msg = o.get("message") or {}
                content = msg.get("content")
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = " ".join(c.get("text", "") for c in content
                                    if isinstance(c, dict) and c.get("type") == "text")
                if len(text) < 10 or "task-notification" in text:
                    continue
                if _is_meta_text(text):
                    continue
                n_match = sum(1 for w in words if w in text)
                if n_match >= max(1, len(words) // 3):
                    ts = o.get("timestamp") or ""
                    key = (ts[:16], text[:40])
                    if key in exclude_keys:
                        continue
                    hits.append({"ts": ts, "actor": ACTOR_USER if typ == "user" else ACTOR_AI,
                                 "text": text, "n_match": n_match})
        hits.sort(key=lambda h: h["ts"], reverse=True)
        hits.sort(key=lambda h: -h["n_match"])
        LAST_TAIL.extend(hits[:limit])
    except Exception as ex:
        _note_error("tail", ex)


def _tag_for(r):
    typ = r.get("type") or "text"
    actor = r.get("actor") or ""
    if typ == "action":
        tag = "%s (action)" % actor
    elif typ == "thinking":
        tag = "%s (thinking)" % actor
    else:
        tag = actor
    if r.get("confirm"):
        tag = "[maybe] " + tag
    return tag


# ============================================================
# Optional "compaction" feature: for a caller whose ingest format
# ("claude_code") marks context-compaction boundaries, --compact[=N]
# returns everything said since the N-th-to-last boundary. Depends on
# query_rules.compact_range; a no-op (with a clear message) if
# query_rules isn't available or the session can't be identified.
# ============================================================
def _detect_session():
    """Guess the current session id from the newest file in
    data_dir/main, when --session isn't given."""
    try:
        main_dir = os.path.join(data_dir(), "main")
        files = sorted(glob.glob(os.path.join(main_dir, "*.jsonl")),
                       key=os.path.getmtime, reverse=True)
        for fp in files:
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except Exception:
                continue
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("session"):
                    return d.get("session")
    except Exception:
        pass
    return None


def _compact_lines(session, n):
    lines = []
    if _qr is None:
        lines.append("query_rules is unavailable, so compaction boundaries can't be read.")
        return lines
    sess = session or _detect_session()
    if not sess:
        lines.append("Couldn't identify a session (pass --session=ID to specify one).")
        return lines
    try:
        start, end = _qr.compact_range(sess, int(n))
    except Exception as ex:
        _note_error("compact_range", ex)
        lines.append("Couldn't read compaction boundaries.")
        return lines
    if start is None:
        lines.append("This session has %d compaction boundary(ies)." % end)
        return lines
    try:
        s_dt = datetime.datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        e_dt = datetime.datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        if s_dt.tzinfo is None:
            s_dt = s_dt.replace(tzinfo=datetime.timezone.utc)
        if e_dt.tzinfo is None:
            e_dt = e_dt.replace(tzinfo=datetime.timezone.utc)
        rows, _meta = index_exact.search_time((s_dt, e_dt), None, [])
    except Exception as ex:
        _note_error("compact(search_time)", ex)
        rows = []
    lines.append("-- before compaction: %d row(s) [%s, %s) --" % (len(rows), _ts_jst(start), _ts_jst(end)))
    for h in rows:
        ts = _ts_jst(h.get("ts"))
        tag = _tag_for(h)
        text = (h.get("text") or "").replace("\n", " ")
        lines.append("[%s] %s: %s" % (ts, tag, text))
    return lines


def recall(query, limit=6, recency=True, actor=None, around=0, full=False,
          compact_n=0, session=None):
    """The manual entry point. Calls fetch() and formats the result
    for display. Returns a list of display lines, [NOW] first."""
    del LAST_MODE[:]
    now = datetime.datetime.now(user_tz()).strftime("%Y-%m-%d %H:%M")
    lines = ["[NOW] " + now + " -- anchor on the current time before reading anything below."]

    if compact_n:
        lines.extend(_compact_lines(session, compact_n))
        return lines

    _dr, _tr, _kws = _split_time(query)
    if _dr is None and _tr is not None:
        lines.append("A time-of-day alone can't narrow the range (searching all time). "
                     "Add a date to narrow it (e.g. \"2026-09-02 evening budget\").")

    uniq = fetch(query, actor=actor, around=around, log_limit=limit, recency=recency)
    if "exact-only" in LAST_MODE:
        lines.append("Semantic search is unavailable right now; showing exact date/keyword matches only.")

    if LAST_TAIL and "time" not in LAST_MODE:
        lines.append("-- most recent (not yet indexed) --")
        for h in LAST_TAIL:
            ts = _ts_jst(h.get("ts"))
            text = (h.get("text") or "").replace("\n", " ")[:200]
            lines.append("[%s] %s: %s" % (ts, h.get("actor", ""), text))

    if uniq:
        ordered = sorted(uniq, key=lambda r: (r.get("ts") or ""))
        texts_show = []
        for h in ordered:
            t = (h.get("text") or "").replace("\n", " ")
            if not full and len(t) > 300:
                t = t[:300] + "..."
            texts_show.append(t)
        total_chars = sum(len(t) for t in texts_show)
        cut_note = "" if full else " (truncated to 300 chars)"

        if "time" in LAST_MODE:
            meta0 = LAST_TIME_META[0] if LAST_TIME_META else {}
            types = meta0.get("types") or {}
            _label = {"text": "message", "action": "action", "thinking": "thinking", "meta": "meta"}
            _type_parts = ", ".join("%s %d" % (_label.get(k, k), v) for k, v in types.items())
            _head = "-- date-scoped: %d row(s), ~%d chars%s (chronological)" % (len(ordered), total_chars, cut_note)
            if _type_parts:
                _head += " -- " + _type_parts
            lines.append(_head + " --")
            if meta0.get("level") == "L2":
                lines.append("Few keyword matches, so showing the whole range chronologically instead.")
            if meta0.get("fallback_day"):
                lines.append("No date was given, so showing the most recent %s that matches." % meta0["fallback_day"])
        else:
            lines.append("-- %d row(s), ~%d chars%s (verbatim, timestamped, chronological) --"
                        % (len(ordered), total_chars, cut_note))
        if "L3" in LAST_MODE:
            lines.append("Nothing in that date range, so searching all time by meaning instead.")

        for h, t in zip(ordered, texts_show):
            ts = _ts_jst(h.get("ts"))
            tag = _tag_for(h)
            lines.append("[%s] %s: %s" % (ts, tag, t))

        _days = {}
        _kw_total = None
        if "time" in LAST_MODE and LAST_TIME_META:
            _days = dict(LAST_TIME_META[0].get("days") or {})
            _kw_total = LAST_TIME_META[0].get("kw_total")
        else:
            for h in ordered:
                d = _ts_jst(h.get("ts"))[:10]
                _days[d] = _days.get(d, 0) + 1
        if len(_days) > 1:
            _pre = ""
            if _kw_total and _kw_total > len(ordered):
                _pre = "keyword matches: %d total -- " % _kw_total
            lines.append("-- " + _pre + "days matched: "
                         + " / ".join("%s (%d)" % (d, n) for d, n in sorted(_days.items()))
                         + " -- spread across multiple days; naming one would narrow this")

    if "time" in LAST_MODE and not uniq:
        _now_utc = datetime.datetime.now(datetime.timezone.utc)
        if _dr is not None and _dr[0] > _now_utc:
            lines.append("That range is in the future -- there's nothing recorded yet.")
        else:
            lines.append("Nothing on record in that range (the range itself resolved correctly). "
                         "Try different wording or double-check the date.")
    if LAST_ERRORS:
        lines.append("Note: part of the search failed (the index may be mid-rebuild). Results below are incomplete:")
        for e in LAST_ERRORS:
            lines.append("  " + e)
    if len(lines) == 1:
        lines.append("No hits. Absence is reported as absence, never guessed around -- "
                     "but try at least one more phrasing (a different name for the same thing) before concluding there's nothing.")
    return lines


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    argv = sys.argv[1:]
    args = [a for a in argv if not a.startswith("--")]
    actor = None
    if "--user" in argv:
        actor = ACTOR_USER
    elif "--ai" in argv:
        actor = ACTOR_AI
    around = 0
    for a in argv:
        if a.startswith("--around"):
            try:
                around = int(a.split("=", 1)[1]) if "=" in a else 1
            except ValueError:
                around = 1
            around = max(0, around)
    full = "--full" in argv
    compact_n = 0
    for i, a in enumerate(argv):
        if a.startswith("--compact"):
            try:
                if "=" in a:
                    compact_n = int(a.split("=", 1)[1])
                elif i + 1 < len(argv) and argv[i + 1].isdigit():
                    compact_n = int(argv[i + 1])
                else:
                    compact_n = 1
            except ValueError:
                compact_n = 1
    session = None
    for a in argv:
        if a.startswith("--session="):
            session = a.split("=", 1)[1]

    if compact_n:
        for line in recall("", compact_n=compact_n, session=session):
            print(line)
        return

    if not args or not args[0].strip():
        print('usage: python -m lossless_memory.recall "query" [options]')
        print("The one entry point for pulling memory back out; don't call index_exact / index_vector directly.")
        print("")
        print('The strongest query is "date + word(s)": naming a date scopes the search to that range only.')
        print('  example: python -m lossless_memory.recall "2026-09-02 budget"')
        print('           python -m lossless_memory.recall "2026-09-02"   <- no words = show the whole day')
        print("  dates understood: an explicit YYYY-MM-DD / M/D, or a relative phrase in Japanese or English")
        print("  (today, yesterday, 3 days ago, last week, in July, July 19, this morning, around 3pm, ...).")
        print('  Days follow "timezone" in config.json (an IANA name; unset = this machine\'s local time).')
        print("")
        print("--around[=N]  = also show N rows before/after each hit (default 1, no cap)")
        print("--user        = only rows from the configured user_name")
        print("--ai          = only rows from the configured ai_name")
        print("--all-time    = no recency decay (weigh old and new equally)")
        print("--full        = don't truncate rows to 300 chars")
        print("--compact[=N] = everything since the N-th-to-last compaction boundary (claude_code format only)")
        print("--session=ID  = session id to use with --compact (default: auto-detected)")
        sys.exit(1)

    query = " ".join(args)
    lines = recall(query, recency=("--all-time" not in sys.argv),
                   actor=actor, around=around, full=full)
    for line in lines:
        print(line)


if __name__ == "__main__":
    main()
