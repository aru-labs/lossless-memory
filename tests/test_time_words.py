# -*- coding: utf-8 -*-
"""Relative time words in English (and that Japanese still works), and
the configurable timezone. "Now" is pinned so these never depend on the
day the tests run."""
import json
import shutil
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from lossless_memory import ingest, index_exact, recall
from lossless_memory.config import config

SAMPLE_LOG = Path(__file__).resolve().parent.parent / "examples" / "sample_log.jsonl"
NY = ZoneInfo("America/New_York")
TOKYO = ZoneInfo("Asia/Tokyo")
UTC = ZoneInfo("UTC")


def _setup(tmp_path, monkeypatch, tz, today):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    shutil.copy(SAMPLE_LOG, raw_dir / "sample_log.jsonl")
    cfg = {"user_name": "Sam", "ai_name": "Nova", "data_dir": "./logs",
           "raw_log_dir": str(raw_dir), "ingest_format": "plain", "timezone": tz}
    (tmp_path / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    config(force_reload=True)
    monkeypatch.setattr(index_exact, "_today", lambda: today)


@pytest.fixture()
def ny(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, "America/New_York", date(2026, 9, 24))
    yield tmp_path


def _day(y, m, d, tz=NY):
    start = datetime(y, m, d, 0, 0, tzinfo=tz).astimezone(UTC)
    end = datetime(y, m, d, 23, 59, 59, 999999, tzinfo=tz).astimezone(UTC)
    return start, end


def _span(first, last, tz=NY):
    return _day(*first, tz=tz)[0], _day(*last, tz=tz)[1]


@pytest.mark.parametrize("phrase, expected", [
    ("yesterday", (2026, 9, 23)),
    ("what did we say yesterday about the budget", (2026, 9, 23)),
    ("yesterday's notes", (2026, 9, 23)),
    ("today", (2026, 9, 24)),
    ("tonight", (2026, 9, 24)),
    ("this morning", (2026, 9, 24)),
    ("last night", (2026, 9, 23)),
    ("the day before yesterday", (2026, 9, 22)),
    ("3 days ago", (2026, 9, 21)),
    ("three days ago", (2026, 9, 21)),
    ("a day ago", (2026, 9, 23)),
    ("July 19", (2026, 7, 19)),
    ("jul 19th", (2026, 7, 19)),
    ("19 July", (2026, 7, 19)),
    ("the 19th of July", (2026, 7, 19)),
])
def test_english_single_days(ny, phrase, expected):
    assert index_exact._extract_date(phrase) == _day(*expected)


def test_english_ranges(ny):
    assert index_exact._extract_date("last week") == _span((2026, 9, 17), (2026, 9, 23))
    assert index_exact._extract_date("2 weeks ago") == _span((2026, 9, 7), (2026, 9, 13))
    assert index_exact._extract_date("last month") == _span((2026, 8, 1), (2026, 8, 31))
    assert index_exact._extract_date("a month ago") == _span((2026, 8, 1), (2026, 8, 31))
    assert index_exact._extract_date("the month before last") == _span((2026, 7, 1), (2026, 7, 31))
    assert index_exact._extract_date("in July") == _span((2026, 7, 1), (2026, 7, 31))
    # a month later than this one means last year's
    assert index_exact._extract_date("in October") == _span((2025, 10, 1), (2025, 10, 31))
    assert index_exact._extract_date("last year") == _span((2025, 1, 1), (2025, 12, 31))
    assert index_exact._extract_date("the year before last") == _span((2024, 1, 1), (2024, 12, 31))


@pytest.mark.parametrize("phrase", [
    "you may remember the budget",
    "we will march on",
    "the budget",
    "good morning",
])
def test_ordinary_words_are_not_dates(ny, phrase):
    assert index_exact._extract_date(phrase) is None


@pytest.mark.parametrize("phrase, expected", [
    ("yesterday evening", (17, 21)),
    ("this morning", (5, 11)),
    ("last night", (18, 24)),
    ("in the afternoon", (12, 18)),
    ("around 3pm", (14, 17)),
    ("at 9 am", (9, 10)),
    ("12am", (0, 1)),
    ("late at night", (22, 24)),
])
def test_english_time_of_day(ny, phrase, expected):
    assert index_exact._extract_time_range(phrase) == expected


def test_greetings_are_not_times(ny):
    assert index_exact._extract_time_range("good morning, about the budget") is None
    assert index_exact._extract_time_range("goodnight") is None


def test_time_words_do_not_become_keywords(ny):
    dr, tr, kws = index_exact._split_time_query("what did we say yesterday evening about the budget")
    assert dr == _day(2026, 9, 23)
    assert tr == (17, 21)
    assert kws == ["budget"]


def test_first_time_widens_to_all_time(ny):
    dr, _tr, kws = index_exact._split_time_query("the first time we talked about the budget last week")
    assert dr is None
    assert kws == ["budget"]


def test_new_york_day_starts_at_local_midnight(ny):
    start, _end = index_exact._extract_date("yesterday")
    assert start == datetime(2026, 9, 23, 4, 0, tzinfo=UTC)  # EDT is UTC-4


def test_japanese_still_works_in_tokyo(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, "Asia/Tokyo", date(2026, 9, 24))
    assert index_exact._extract_date("昨日の予算") == _day(2026, 9, 23, tz=TOKYO)
    assert index_exact._extract_date("3日前") == _day(2026, 9, 21, tz=TOKYO)
    start, _end = index_exact._extract_date("yesterday")
    assert start == datetime(2026, 9, 22, 15, 0, tzinfo=UTC)  # JST is UTC+9


def test_recall_yesterday_end_to_end(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, "America/New_York", date(2026, 9, 3))
    ingest.convert_all(fmt="plain")
    index_exact.build_index()
    lines = recall.recall("what did we say yesterday about the budget")
    hit_lines = [l for l in lines if l.startswith("[2026-")]
    assert hit_lines
    assert all(l.startswith("[2026-09-02") for l in hit_lines)
    assert any("budget" in l.lower() for l in hit_lines)
