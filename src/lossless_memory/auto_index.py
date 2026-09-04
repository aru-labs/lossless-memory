# -*- coding: utf-8 -*-
"""auto_index -- run the whole indexing pipeline in one call: ingest ->
exact index -> vector index.

Meant to be called at the start of a session and/or periodically by
daemon.py, so whatever was said since the last run is searchable by
the time it's needed. Idempotent (running it again with nothing new
does nothing); a change-detection stamp based on the raw source
files' mtimes lets it skip the (comparatively expensive) vector
embedding step entirely when nothing changed.
"""
import os
import glob

from .config import config, data_dir
from . import ingest
from . import index_exact
from . import index_vector


def _stamp_path():
    return os.path.join(data_dir(), ".last_index_stamp")


def _logs_signature(src_dir):
    """Sum of the source files' mtimes -- a cheap fingerprint of
    "has anything changed"."""
    total = 0.0
    if not src_dir:
        return "0"
    for fp in glob.glob(os.path.join(src_dir, "*.jsonl")):
        try:
            total += os.path.getmtime(fp)
        except OSError:
            pass
    return f"{total:.0f}"


def _changed(src_dir):
    sig = _logs_signature(src_dir)
    old = ""
    stamp = _stamp_path()
    if os.path.exists(stamp):
        try:
            old = open(stamp, encoding="utf-8").read().strip()
        except OSError:
            pass
    return sig != old, sig


def run(fmt=None, force=False, quiet=True):
    """Run the full pipeline if the source logs changed (or force=True).
    Returns (updated: bool, message: str)."""
    cfg = config()
    fmt = fmt or cfg.get("ingest_format") or "plain"
    src_dir = cfg.get("raw_log_dir")

    changed, sig = _changed(src_dir)
    if not changed and not force:
        return False, "index: no change (skipped)"

    if force:
        total, _skipped, _days = ingest.convert_all(fmt=fmt, source=src_dir)
    else:
        total, _full = ingest.convert_incremental(fmt=fmt, source=src_dir)

    n_exact = index_exact.build_index()
    n_vec = index_vector.build_index(progress=not quiet)

    os.makedirs(os.path.dirname(_stamp_path()), exist_ok=True)
    with open(_stamp_path(), "w", encoding="utf-8") as f:
        f.write(sig)

    return True, (f"index updated: {total} row(s) converted"
                  f" / exact {n_exact} / semantic {n_vec}")


def main():
    import sys
    force = "--force" in sys.argv
    updated, msg = run(force=force, quiet=False)
    print(msg)


if __name__ == "__main__":
    main()
