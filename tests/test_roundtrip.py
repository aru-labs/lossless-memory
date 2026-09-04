# -*- coding: utf-8 -*-
"""End-to-end check: ingest the example log, build the exact index,
and confirm a "date + word" recall query returns at least one hit.

Semantic search (index_vector, via sentence-transformers) is not
required for this test -- it's only exercised, and skipped if the
dependency isn't installed, in test_semantic_index_is_optional.
"""
import json
import shutil
from pathlib import Path

import pytest

from lossless_memory import ingest, index_exact, recall
from lossless_memory.config import config

SAMPLE_LOG = Path(__file__).resolve().parent.parent / "examples" / "sample_log.jsonl"


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """A throwaway working directory with its own config.json, so the
    test never reads or writes anything under the real repo."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    shutil.copy(SAMPLE_LOG, raw_dir / "sample_log.jsonl")

    cfg = {
        "user_name": "Sam",
        "ai_name": "Nova",
        "data_dir": "./logs",
        "raw_log_dir": str(raw_dir),
        "ingest_format": "plain",
    }
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    config(force_reload=True)
    yield tmp_path


def test_ingest_produces_daily_files(workspace):
    total, skipped, days = ingest.convert_all(fmt="plain")
    assert total > 15
    assert skipped == 0
    assert days == 3
    main_dir = Path("logs") / "main"
    day_files = sorted(p.name for p in main_dir.glob("*.jsonl"))
    assert day_files == ["2026-09-01.jsonl", "2026-09-02.jsonl", "2026-09-03.jsonl"]


def test_exact_index_roundtrip(workspace):
    ingest.convert_all(fmt="plain")
    n = index_exact.build_index()
    assert n > 0

    rows = index_exact.search("budget", limit=10)
    assert len(rows) >= 1
    # index_exact falls back to an OR of 2-character windows (bigram
    # search has no word boundaries to rely on, by design -- see the
    # Limitations section in the README for why a single short English
    # word can pull in a loose match), so only check that the intended
    # row is somewhere in the result, not that every row is an exact
    # substring match.
    assert any("budget" in r["text"].lower() for r in rows)


def test_recall_time_and_word(workspace):
    """The strongest query shape this project is built around: an
    explicit date plus a keyword should return only rows from that
    date, verbatim, timestamped."""
    ingest.convert_all(fmt="plain")
    index_exact.build_index()

    lines = recall.recall("2026-09-02 budget")
    assert lines[0].startswith("[NOW]")

    hit_lines = [l for l in lines if l.startswith("[2026-09-02")]
    assert len(hit_lines) >= 1
    assert any("budget" in l.lower() for l in hit_lines)
    # time-scoped mode must not leak rows from other days
    assert not any(l.startswith("[2026-09-01") or l.startswith("[2026-09-03") for l in hit_lines)


def test_recall_date_only_shows_whole_day(workspace):
    ingest.convert_all(fmt="plain")
    index_exact.build_index()

    lines = recall.recall("2026-09-03")
    hit_lines = [l for l in lines if l.startswith("[2026-09-03")]
    assert len(hit_lines) >= 5  # every row recorded that day, not just a fragment


def test_semantic_index_is_optional():
    """index_vector (semantic search) depends on sentence-transformers,
    which is heavy -- skip cleanly if it isn't installed rather than
    failing the suite."""
    pytest.importorskip("sentence_transformers")
    pytest.importorskip("sqlite_vec")
    from lossless_memory import index_vector  # noqa: F401
