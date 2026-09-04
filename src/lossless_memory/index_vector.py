# -*- coding: utf-8 -*-
"""index_vector -- semantic recall (the "fuzzy" side), CPU by default.

Sits next to index_exact.py's exact (FTS5) path. Embeds the converted
logs with a multilingual sentence-transformers model into sqlite-vec,
and embeds the user's query the same way, so rows can be found by
meaning even when the wording doesn't match (index_exact can't catch
those).

Runs on CPU by default -- it never touches a GPU unless explicitly
asked to (see LM_VEC_GPU below), so it can't interfere with anything
else using the GPU on the same machine.

The converted logs (<data_dir>/main) are the source of truth and are
only read here. The vector database is a derived artifact: delete it
and build_index() rebuilds it from the logs.

e5-family models expect "passage: " on stored text and "query: " on
the search text; this improves retrieval quality noticeably.
"""
import os
import json
import glob
import struct
import sqlite3
import hashlib

# GPU opt-in: set LM_VEC_GPU to a CUDA device index (e.g. "0") to let
# only this process embed on that GPU. Leave it unset (the default) to
# force CPU, regardless of any global CUDA_VISIBLE_DEVICES the rest of
# the environment has set.
_GPU = os.environ.get("LM_VEC_GPU", "")
os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
os.environ["CUDA_VISIBLE_DEVICES"] = _GPU  # "" => hide all GPUs => CPU
import sqlite_vec
from sentence_transformers import SentenceTransformer

from .config import data_dir

# Model profiles: LM_VEC_MODEL selects one by name. Each profile has
# its own database file, so switching profiles never touches another
# profile's index -- you can always switch back.
_PROFILES = {
    "e5": {
        "model": "intfloat/multilingual-e5-small", "dim": 384,
        "q_prefix": "query: ", "p_prefix": "passage: ", "db": "vector.db",
    },
}
_ACTIVE = os.environ.get("LM_VEC_MODEL", "e5")
_P = _PROFILES.get(_ACTIVE, _PROFILES["e5"])

_MODEL_NAME = _P["model"]
_DIM = _P["dim"]
_model = None


def _vec_db():
    return os.path.join(data_dir(), _P["db"])


def _log_main():
    return os.path.join(data_dir(), "main")


def _log_corpus():
    return os.path.join(data_dir(), "corpus")


def _get_model():
    global _model
    if _model is None:
        dev = "cpu"
        if _GPU:
            try:
                import torch
                if torch.cuda.is_available():
                    dev = "cuda"
            except Exception:
                dev = "cpu"  # GPU was requested but torch has no CUDA build -- fall back quietly
        _model = SentenceTransformer(_MODEL_NAME, device=dev)
    return _model


def embed(text, is_query=False):
    """Embed text into a `dim`-length vector (list of floats). None on
    failure or empty input."""
    if not text or not text.strip():
        return None
    try:
        prefix = _P["q_prefix"] if is_query else _P["p_prefix"]
        v = _get_model().encode(prefix + text[:2000], normalize_embeddings=True)
        return v.tolist()
    except Exception:
        return None


def _pack(vec):
    return struct.pack(f"{len(vec)}f", *vec)


def _connect():
    con = sqlite3.connect(_vec_db(), timeout=30)
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    con.enable_load_extension(False)
    con.execute("PRAGMA journal_mode=WAL")  # don't block readers while writing
    con.execute("PRAGMA busy_timeout=30000")
    return con


def _day_of(fp):
    """Turn a source path into a (source, day) key, e.g.
    logs/main/2026-07-06.jsonl -> "main/2026-07-06",
    logs/corpus/notes/2026-07-06.jsonl -> "notes/2026-07-06"."""
    day = os.path.splitext(os.path.basename(fp))[0]
    parent = os.path.basename(os.path.dirname(fp))
    if parent == "main":
        return f"main/{day}"
    return f"{parent}/{day}"


def _ensure_schema(con):
    con.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS vec USING vec0(embedding float[{_DIM}])")
    # line_no = which physical line of that day's file this row came
    # from (0-based; blank/broken lines still count, so appended lines
    # never shift position).
    con.execute("CREATE TABLE IF NOT EXISTS meta (rowid INTEGER PRIMARY KEY, ts TEXT, actor TEXT, text TEXT, session TEXT, src_day TEXT, line_no INTEGER)")
    # Per-day progress: how many lines of that day have been embedded
    # already (the core of line-level incremental indexing).
    con.execute("CREATE TABLE IF NOT EXISTS day_progress (day TEXT PRIMARY KEY, done_lines INTEGER, sig TEXT)")
    cols = [r[1] for r in con.execute("PRAGMA table_info(day_progress)").fetchall()]
    if "sig" not in cols:
        con.execute("ALTER TABLE day_progress ADD COLUMN sig TEXT")
    con.execute("CREATE INDEX IF NOT EXISTS idx_meta_day ON meta(src_day)")


def _should_embed(rtype):
    """Only text/doc/action rows are embedded; meta (compaction
    boundaries) and thinking rows are exact-search only."""
    return rtype not in ("meta", "thinking")


def _next_rowid(con):
    r = con.execute("SELECT MAX(rowid) FROM meta").fetchone()
    return (r[0] or 0) + 1


def _schema_is_current(con):
    cols = [r[1] for r in con.execute("PRAGMA table_info(meta)").fetchall()]
    if "line_no" not in cols:
        return False
    t = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='day_progress'"
    ).fetchone()
    return t is not None


def _prefix_sig(lines, k):
    if k <= 0 or k > len(lines):
        return ""
    h = hashlib.sha1()
    for l in lines[:k]:
        h.update(l.encode("utf-8", "replace"))
    return h.hexdigest()[:16]


def _delete_day(con, day):
    rids = [r[0] for r in con.execute("SELECT rowid FROM meta WHERE src_day = ?", (day,)).fetchall()]
    for rid in rids:
        con.execute("DELETE FROM vec WHERE rowid = ?", (rid,))
    con.execute("DELETE FROM meta WHERE src_day = ?", (day,))
    con.execute("DELETE FROM day_progress WHERE day = ?", (day,))
    con.commit()
    return len(rids)


def build_index(progress=True, force=False):
    """Line-level incremental index: for each day, embed only the
    lines added since the last run. Past rows and the database file
    itself are never deleted outright (only replaced per-day when a
    file was rewritten), so this can run safely alongside a daemon.
    Returns the current total row count."""
    vec_db = _vec_db()
    os.makedirs(os.path.dirname(vec_db), exist_ok=True)

    con = _connect()

    # An old schema (missing line_no / day_progress) is rebuilt inside
    # the database only -- the file itself is never deleted, so a
    # daemon holding it open on Windows won't error out.
    need_rebuild = force
    if os.path.exists(vec_db):
        try:
            _ensure_schema(con)
            if not _schema_is_current(con):
                need_rebuild = True
        except Exception:
            need_rebuild = True

    if need_rebuild:
        con.execute("DROP TABLE IF EXISTS vec")
        con.execute("DROP TABLE IF EXISTS meta")
        con.execute("DROP TABLE IF EXISTS day_progress")
        con.execute("DROP TABLE IF EXISTS day_sig")
        con.commit()

    _ensure_schema(con)

    # One-time migration: prefix legacy bare-day keys with "main/".
    # Idempotent -- only rows without a "/" are touched.
    con.execute("UPDATE day_progress SET day = 'main/' || day WHERE day NOT LIKE '%/%'")
    con.execute("UPDATE meta SET src_day = 'main/' || src_day WHERE src_day NOT LIKE '%/%'")
    con.commit()

    progress_map = {}
    for _day, _done, _sig in con.execute("SELECT day, done_lines, sig FROM day_progress").fetchall():
        progress_map[_day] = (_done or 0, _sig or "")

    model = None
    BATCH = 64
    added_total = 0
    seen = set()

    _files = sorted(glob.glob(os.path.join(_log_main(), "*.jsonl")))
    _files += sorted(glob.glob(os.path.join(_log_corpus(), "*", "*.jsonl")))
    for fp in _files:
        day = _day_of(fp)
        with open(fp, encoding="utf-8") as f:
            lines = f.readlines()
        cur = len(lines)
        done, sig = progress_map.get(day, (0, ""))
        seen.add(day)

        # A leftover row whose line number is beyond the file's
        # current length can only be stale (the file got rewritten
        # shorter at some point) -- drop it and re-index the day.
        orphan = con.execute(
            "SELECT 1 FROM meta WHERE src_day = ? AND line_no >= ? LIMIT 1", (day, cur)
        ).fetchone()
        if orphan is not None:
            n = _delete_day(con, day)
            if progress:
                print(f"  index dropped: {day} (-{n} rows; a line number past the current length was found)")
            done, sig = 0, ""
        else:
            # Two rows sharing the same line number for the same day
            # is also only possible if one of them is stale.
            dup = con.execute(
                "SELECT 1 FROM meta WHERE src_day = ? GROUP BY line_no HAVING COUNT(*) > 1 LIMIT 1", (day,)
            ).fetchone()
            if dup is not None:
                n = _delete_day(con, day)
                if progress:
                    print(f"  index dropped: {day} (-{n} rows; a duplicate line number was found)")
                done, sig = 0, ""

        if cur == done and _prefix_sig(lines, cur) == sig:
            continue  # unchanged
        if sig == "":
            if cur < done:
                n = _delete_day(con, day)
                if progress:
                    print(f"  index dropped: {day} (-{n} rows; file was rewritten)")
                done = 0
        elif cur < done or _prefix_sig(lines, done) != sig:
            n = _delete_day(con, day)
            if progress:
                print(f"  index dropped: {day} (-{n} rows; file was rewritten)")
            done = 0

        new_items = []      # (line_no, record) for new lines eligible for embedding
        all_texts = {}       # line_no -> text, type=="text" rows only (used for the context window)
        total_lines = 0
        for idx, raw in enumerate(lines):
            total_lines = idx + 1
            line = raw.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = r.get("text", "").strip()
            if not t:
                continue
            rtype = r.get("type", "text")
            if rtype == "text":
                # Only type=="text" rows feed the context window, so a
                # tool-call action row never gets stitched into the
                # embedded neighborhood of a real message.
                all_texts[idx] = t
            if idx >= done:
                if not _should_embed(rtype):
                    continue
                new_items.append((idx, r))

        if new_items:
            if model is None:
                model = _get_model()
            rid = _next_rowid(con)
            # Context window: embed "previous message (400 chars) +
            # this row (1600 chars) + next message (400 chars)" so a
            # short reply still carries the meaning of what it was
            # replying to. The returned row itself is always the
            # unmodified original text -- only the embedding uses the
            # window.
            _keys = sorted(all_texts.keys())
            _pos = {k: n for n, k in enumerate(_keys)}
            def _ctx_window(line_no, own_text):
                n = _pos.get(line_no)
                parts = []
                if n is not None and n > 0:
                    parts.append(all_texts[_keys[n - 1]][:400])
                parts.append(own_text[:1600])
                if n is not None and n + 1 < len(_keys):
                    parts.append(all_texts[_keys[n + 1]][:400])
                return "\n".join(parts)
            for i in range(0, len(new_items), BATCH):
                chunk = new_items[i:i + BATCH]
                texts = [_P["p_prefix"] + _ctx_window(ln, r.get("text", "")) for ln, r in chunk]
                vecs = model.encode(texts, normalize_embeddings=True, batch_size=BATCH)
                for (line_no, r), v in zip(chunk, vecs):
                    con.execute("INSERT INTO vec(rowid, embedding) VALUES (?, ?)", (rid, _pack(v.tolist())))
                    con.execute(
                        "INSERT INTO meta(rowid, ts, actor, text, session, src_day, line_no) VALUES (?,?,?,?,?,?,?)",
                        (rid, r.get("ts"), r.get("actor"), r.get("text"), r.get("session"), day, line_no),
                    )
                    rid += 1
            con.commit()
            added_total += len(new_items)
            if progress:
                print(f"  index added: {day} (+{len(new_items)} rows / {done}->{total_lines} lines)")

        con.execute(
            "INSERT OR REPLACE INTO day_progress(day, done_lines, sig) VALUES (?,?,?)",
            (day, total_lines, _prefix_sig(lines, total_lines)),
        )
        con.commit()

    # A day that used to have rows but wasn't seen this pass means its
    # source file disappeared -- drop its rows too.
    for day in list(progress_map.keys()):
        if day not in seen:
            _delete_day(con, day)

    con.commit()
    total = con.execute("SELECT COUNT(*) FROM meta").fetchone()[0]
    con.close()
    if progress and added_total == 0:
        print("  vector index: no new rows (fully skipped)")
    return total


def search(query, limit=5):
    """Semantic search: rows closest in meaning to query."""
    if not os.path.exists(_vec_db()):
        return []
    qv = embed(query, is_query=True)
    if qv is None:
        return []
    con = _connect()
    try:
        rows = con.execute(
            "SELECT v.distance, m.ts, m.actor, m.text, m.session"
            " FROM vec v JOIN meta m ON m.rowid = v.rowid"
            " WHERE v.embedding MATCH ? AND k = ? ORDER BY v.distance",
            (_pack(qv), int(limit)),
        ).fetchall()
        return [
            {"distance": r[0], "ts": r[1], "actor": r[2], "text": r[3], "session": r[4]}
            for r in rows
        ]
    finally:
        con.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        _dev = str(_get_model().device)
        if _dev.startswith("cuda"):
            import torch
            _dev += " " + torch.cuda.get_device_name(0)
        print("=== index_vector build (%s) ===" % _dev)
        n = build_index()
        print(f"embedded: {n} rows")
    elif len(sys.argv) > 2 and sys.argv[1] == "search":
        for h in search(sys.argv[2], limit=5):
            print(f"[{h['ts'][:16]}] ({h['distance']:.3f}) {h['actor']}: {h['text'][:70]}")
    else:
        print("usage: python -m lossless_memory.index_vector build  /  ... search <query>")
