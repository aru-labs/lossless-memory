# -*- coding: utf-8 -*-
"""Convert raw conversation logs into the lossless 7-field record format.

Two input formats are supported:

  "claude_code" -- Claude Code's own per-session JSONL transcripts (the
      files it writes under its own projects directory). Reads
      message.content, keeps user/assistant text, and also emits a
      type="action" row for every tool_use block so tool calls stay in
      the timeline. Lines that look like a context-compaction boundary
      are tagged type="meta" (see query_rules.compact_range).

  "plain" -- a generic {"ts": ISO8601, "role": "user"|"assistant",
      "text": "..."} JSON-lines file. This is the format most other
      integrations should produce; it has no notion of tool calls.

Both produce the same seven core fields per row (ts, actor, role, type,
text, model, session), grouped into daily files under
<data_dir>/main/YYYY-MM-DD.jsonl.

The source files are never modified -- this module only reads them.
Re-running convert_all() is idempotent (it rewrites the daily files
from scratch); convert_incremental() only reads the bytes appended
since the last run and is safe to call often (e.g. from daemon.py).
"""
import os
import re
import json
import glob
from datetime import datetime, timedelta

from .config import config, data_dir

# Rows whose text matches these are tagged type="meta" instead of "text"
# (e.g. a context-compaction boundary, or a harness-injected command
# echo) so search can exclude them by default.
META_HEADS = ("This session is being continued",)
META_MARKS = ("<command-name>", "<local-command-stdout>", "<task-notification>")

# Day bucketing uses the same +9h (JST) offset as index_exact.py's date
# vocabulary, so "which file a row lands in" and "which day a search
# for that row resolves to" agree with each other.
_DAY_OFFSET_HOURS = 9


def _out_main_dir():
    d = os.path.join(data_dir(), "main")
    os.makedirs(d, exist_ok=True)
    return d


def _state_file():
    return os.path.join(data_dir(), "_ingest_state.json")


def _is_meta(text):
    if any(text.startswith(h) for h in META_HEADS):
        return True
    if any(m in text for m in META_MARKS):
        return True
    return False


def _day_of_ts(ts):
    try:
        d = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S") + timedelta(hours=_DAY_OFFSET_HOURS)
        return d.strftime("%Y-%m-%d")
    except Exception:
        return ts[:10]


def _clean(text):
    """Strip harness-injected noise (e.g. a caveat block) without
    touching the meaning of the actual message."""
    if not text:
        return ""
    text = re.sub(r"<local-command-caveat>.*?</local-command-caveat>", "", text, flags=re.S)
    return text.strip()


# ---------------------------------------------------------------------
# "claude_code" format
# ---------------------------------------------------------------------
def _extract_text_claude(content):
    """Pull the text body out of a Claude Code message.content value.
    User content is a plain string; assistant content is a list of
    blocks ({"type": "text", ...} / {"type": "tool_use", ...} / ...).
    Only the text blocks are the message body."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                t = c.get("text", "")
                if t:
                    parts.append(t)
        return "\n".join(parts).strip()
    return ""


def _action_rows_claude(content, ts, session, day, actor_ai, model_ai):
    """One type="action" row per tool_use block (tool_result blocks are
    dropped -- only the call itself is kept)."""
    rows = []
    if not isinstance(content, list):
        return rows
    for c in content:
        if not isinstance(c, dict) or c.get("type") != "tool_use":
            continue
        name = c.get("name") or "tool"
        val = ""
        inp = c.get("input")
        if isinstance(inp, dict):
            for v in inp.values():
                if isinstance(v, str) and v.strip():
                    val = v.strip()
                    break
        text = f"{name}: {val[:200]}"
        rec = {"ts": ts, "actor": actor_ai, "role": "ai", "type": "action",
               "text": text, "model": model_ai, "session": session}
        rows.append((day, ts, json.dumps(rec, ensure_ascii=False) + "\n"))
    return rows


def _parse_claude_line(line, session, actor_user, actor_ai, model_ai):
    line = line.strip()
    if not line:
        return None
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        return None
    if d.get("type") not in ("user", "assistant"):
        return None  # skip harness-internal record types
    msg = d.get("message", {})
    if not isinstance(msg, dict):
        return None
    ts = d.get("timestamp")
    if not ts:
        return None
    role_raw = msg.get("role")
    day = _day_of_ts(ts)
    rows = []

    raw_text = _clean(_extract_text_claude(msg.get("content")))
    if raw_text:
        if role_raw == "user":
            actor, role, model = actor_user, "user", None
        else:
            actor, role, model = actor_ai, "ai", model_ai
        typ = "meta" if _is_meta(raw_text) else "text"
        rec = {"ts": ts, "actor": actor, "role": role, "type": typ,
               "text": raw_text, "model": model, "session": session}
        rows.append((day, ts, json.dumps(rec, ensure_ascii=False) + "\n"))

    if role_raw == "assistant":
        rows.extend(_action_rows_claude(msg.get("content"), ts, session, day, actor_ai, model_ai))

    return rows or None


# ---------------------------------------------------------------------
# "plain" format: {"ts": ..., "role": "user"|"assistant", "text": ...}
# ---------------------------------------------------------------------
def _parse_plain_line(line, session, actor_user, actor_ai, model_ai):
    line = line.strip()
    if not line:
        return None
    try:
        d = json.loads(line)
    except json.JSONDecodeError:
        return None
    ts = d.get("ts")
    text = _clean(str(d.get("text") or ""))
    if not ts or not text:
        return None
    role_raw = d.get("role")
    day = _day_of_ts(ts)
    if role_raw == "user":
        actor, role, model = actor_user, "user", None
    else:
        actor, role, model = actor_ai, "ai", model_ai
    typ = "meta" if _is_meta(text) else "text"
    rec = {"ts": ts, "actor": actor, "role": role, "type": typ,
           "text": text, "model": model, "session": session}
    return [(day, ts, json.dumps(rec, ensure_ascii=False) + "\n")]


_PARSERS = {"claude_code": _parse_claude_line, "plain": _parse_plain_line}


def _names():
    cfg = config()
    return (cfg.get("user_name") or "user", cfg.get("ai_name") or "assistant",
            cfg.get("model_name") or "unknown")


def convert_all(fmt="plain", source=None):
    """Rebuild every daily file in <data_dir>/main from scratch.
    Idempotent: running it again produces the same output. Returns
    (total_rows, skipped_rows, day_file_count)."""
    parser = _PARSERS.get(fmt)
    if parser is None:
        raise ValueError("unknown ingest format: %r (use 'claude_code' or 'plain')" % (fmt,))
    src_dir = source or config().get("raw_log_dir")
    if not src_dir:
        raise ValueError("no source directory given (pass source=... or set raw_log_dir in config)")
    out_main = _out_main_dir()
    actor_user, actor_ai, model_ai = _names()

    files = sorted(glob.glob(os.path.join(src_dir, "*.jsonl")))
    by_date = {}
    total = 0
    skipped = 0
    for fp in files:
        session = os.path.splitext(os.path.basename(fp))[0]
        with open(fp, encoding="utf-8", errors="replace") as f:
            for line in f:
                parsed = parser(line, session, actor_user, actor_ai, model_ai)
                if parsed:
                    for day, ts, out in parsed:
                        by_date.setdefault(day, []).append((ts, out))
                        total += 1
                else:
                    skipped += 1

    # Sort by timestamp before writing: new lines always land at the end
    # of a day file, so a later incremental pass can trust that the
    # bytes before its last-seen position never change.
    for day, lines in by_date.items():
        lines.sort(key=lambda x: x[0])
        with open(os.path.join(out_main, day + ".jsonl"), "w", encoding="utf-8") as f:
            f.writelines(l for _, l in lines)

    return total, skipped, len(by_date)


def _save_positions(fmt, source):
    state = {"fmt": fmt, "files": {}}
    for fp in glob.glob(os.path.join(source, "*.jsonl")):
        try:
            state["files"][fp] = os.path.getsize(fp)
        except OSError:
            pass
    with open(_state_file(), "w", encoding="utf-8") as f:
        json.dump(state, f)


def convert_incremental(fmt="plain", source=None):
    """Convert only the bytes appended to each source file since the
    last call. Falls back to a full convert_all() on the very first
    call, or whenever a source file has shrunk (rotated). Returns
    (new_row_count, did_full_rebuild)."""
    src_dir = source or config().get("raw_log_dir")
    if not src_dir:
        raise ValueError("no source directory given (pass source=... or set raw_log_dir in config)")
    parser = _PARSERS.get(fmt)
    if parser is None:
        raise ValueError("unknown ingest format: %r (use 'claude_code' or 'plain')" % (fmt,))
    out_main = _out_main_dir()

    try:
        with open(_state_file(), encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        state = {}
    if state.get("fmt") != fmt:
        state = {"fmt": fmt, "files": {}}
    if not state.get("files") or not glob.glob(os.path.join(out_main, "*.jsonl")):
        total, _skipped, _days = convert_all(fmt=fmt, source=src_dir)
        _save_positions(fmt, src_dir)
        return total, True

    actor_user, actor_ai, model_ai = _names()
    positions = state["files"]
    new_by_date = {}
    total_new = 0

    for fp in sorted(glob.glob(os.path.join(src_dir, "*.jsonl"))):
        session = os.path.splitext(os.path.basename(fp))[0]
        pos = int(positions.get(fp, 0))
        try:
            size = os.path.getsize(fp)
        except OSError:
            continue
        if size < pos:
            total, _s, _d = convert_all(fmt=fmt, source=src_dir)
            _save_positions(fmt, src_dir)
            return total, True
        if size == pos:
            continue

        skip_first = False
        if pos > 0:
            try:
                with open(fp, "rb") as fb:
                    fb.seek(pos - 1)
                    skip_first = fb.read(1) != b"\n"
            except OSError:
                skip_first = False

        with open(fp, encoding="utf-8", errors="replace") as f:
            f.seek(pos)
            if skip_first:
                f.readline()
            for line in f:
                parsed = parser(line, session, actor_user, actor_ai, model_ai)
                if parsed:
                    for day, ts, out in parsed:
                        new_by_date.setdefault(day, []).append((ts, out))
                        total_new += 1
            positions[fp] = f.tell()

    for day, lines in new_by_date.items():
        lines.sort(key=lambda x: x[0])
        day_path = os.path.join(out_main, day + ".jsonl")
        last_ts = ""
        day_exists = os.path.exists(day_path)
        if day_exists:
            with open(day_path, "rb") as f:
                f.seek(max(0, os.path.getsize(day_path) - 65536))
                tail = f.read().decode("utf-8", errors="ignore").strip().splitlines()
            for tl in reversed(tail):
                try:
                    last_ts = json.loads(tl).get("ts") or ""
                    break
                except Exception:
                    continue
        if day_exists and not last_ts:
            last_ts = "9999"  # forces the merge branch below (safer than a bad append)
        if not last_ts or lines[0][0] >= last_ts:
            with open(day_path, "a", encoding="utf-8") as f:
                f.writelines(l for _, l in lines)
        else:
            merged = []
            if os.path.exists(day_path):
                with open(day_path, encoding="utf-8") as f:
                    for l in f:
                        try:
                            merged.append((json.loads(l).get("ts") or "", l))
                        except Exception:
                            continue
            merged += lines
            merged.sort(key=lambda x: x[0])
            with open(day_path, "w", encoding="utf-8") as f:
                f.writelines(l for _, l in merged)

    state["files"] = positions
    with open(_state_file(), "w", encoding="utf-8") as f:
        json.dump(state, f)
    return total_new, False


def main():
    import sys
    fmt = "plain"
    for a in sys.argv[1:]:
        if a.startswith("--format="):
            fmt = a.split("=", 1)[1]
    total, skipped, days = convert_all(fmt=fmt)
    print(f"converted: {total} rows / skipped (no text): {skipped} / day files: {days}")
    print(f"output: {_out_main_dir()}")


if __name__ == "__main__":
    main()
