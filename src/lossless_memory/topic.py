# -*- coding: utf-8 -*-
"""topic -- manual topic markers for the state_index (LLL) layer.

Call this whenever the conversation changes topic:

    python -m lossless_memory.topic "topic name"

This appends {"ts", "topic"} to data_dir/topics.jsonl, and
state_index.py mixes it into the timeline as a "* topic name" line.

Why a plain append-only log instead of parsing markers out of
response text: that approach caught false positives (a marker
mentioned in passing, inside an explanation) and polluted
text-to-speech output. A dedicated append has neither problem, and
costs nothing extra to write.
"""
import sys
import os
import json
import datetime

from .config import data_dir, user_tz


def _out_path():
    return os.path.join(data_dir(), "topics.jsonl")


def _done_path():
    return os.path.join(data_dir(), "topics_done.jsonl")


def _last_rec():
    """The last line of topics.jsonl, or None."""
    try:
        last = None
        with open(_out_path(), encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    last = ln
        return json.loads(last) if last else None
    except Exception:
        return None


def add(topic):
    rec = {"ts": datetime.datetime.now(user_tz()).strftime("%Y-%m-%dT%H:%M:%S"),
           "topic": topic.strip()[:40]}
    # Skip a duplicate of the immediately preceding topic, no matter
    # how much time has passed -- switching tabs/sessions back and
    # forth otherwise stamps the same topic several times in a row,
    # which adds nothing.
    prev = _last_rec()
    if prev and prev.get("topic") == rec["topic"]:
        rec["skipped"] = True
        return rec
    path = _out_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def done(word):
    """Mark a topic as settled. Nothing is edited or deleted: topics.jsonl
    stays exactly as recorded, and this appends "this word marks a
    settled topic" to a separate log. state_index.py cross-references
    the two to show a checkmark."""
    rec = {"ts": datetime.datetime.now(user_tz()).strftime("%Y-%m-%dT%H:%M:%S"),
           "match": word.strip()[:40]}
    path = _done_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) < 2:
        print('usage: python -m lossless_memory.topic "topic name"')
        print('       python -m lossless_memory.topic done "word contained in the topic"')
        sys.exit(1)
    if sys.argv[1] == "done" and len(sys.argv) >= 3:
        r = done(sys.argv[2])
        print("marked done:", r["ts"][11:16], "topics containing '%s'" % r["match"])
    else:
        r = add(sys.argv[1])
        print("topic recorded:", r["ts"][11:16], r["topic"])


if __name__ == "__main__":
    main()
