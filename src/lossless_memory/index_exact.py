# -*- coding: utf-8 -*-
"""index_exact -- exact-match recall: full-text search plus a temporal
ruler.

Role:
  Reads the converted logs (ingest.py's output, under
  <data_dir>/main/*.jsonl, plus any extra corpora under
  <data_dir>/corpus/<name>/*.jsonl) and searches them "exactly" with
  SQLite FTS5.

Philosophy:
  - This is the exact-match path: keyword / date / time-of-day filters
    return the matching raw rows verbatim, never summarized. No
    hallucination is possible here -- it can only return what is
    literally on record.
  - The converted logs are the source of truth; this module only
    reads them. The search index (index_exact.db) is a derived
    artifact -- delete it and build_index() rebuilds it from the logs.

Japanese support:
  Japanese has no word boundaries, so text is split into bigrams
  (2-character windows) before being stored in FTS5, and queries are
  bigram-split the same way before matching.

Date / time-of-day vocabulary:
  Relative date and time-of-day words are understood in Japanese and
  English ("today" / "yesterday" / "3 days ago" / "last week" /
  "in July" / "July 19" / "this morning" / "around 3pm", and their
  Japanese counterparts). Explicit ISO dates (YYYY-MM-DD) and bare
  M/D dates are language neutral and always work.

  Row timestamps are stored as UTC ISO8601. The words a user speaks
  are interpreted in the configured timezone (config "timezone", an
  IANA name; unset = this machine's local time) and converted before
  comparison, so "today" means today where the user lives, not UTC.
  Helper names below still say "jst" for historical reasons; they all
  use that configured timezone.
"""
import os
import re
import json
import glob
import sqlite3
import hashlib
from datetime import datetime, timedelta, timezone, time as _time

try:
    from . import query_rules as _qr
except Exception:
    _qr = None

from .config import data_dir, user_tz

_LOG_MAIN = None  # resolved lazily so tests can point data_dir() elsewhere
_CORE_FIELDS = ("ts", "actor", "role", "type", "text", "model", "session")

_UTC = timezone.utc


def _tz():
    """The user's timezone (config "timezone"; unset = local time)."""
    return user_tz()


def _today():
    """Today's date in the user's timezone. Kept as its own function so
    tests can pin "now"."""
    return datetime.now(_tz()).date()


def _log_main():
    return os.path.join(data_dir(), "main")


def _log_corpus():
    return os.path.join(data_dir(), "corpus")


def _index_db():
    return os.path.join(data_dir(), "index_exact.db")


# ============================================================
# Query pre-processing dictionaries (filler words, stopwords).
# Defaults live in query_rules.py; a settings file can extend them.
# Time words themselves (today/yesterday/at night/...) are handled
# separately below and are *not* filtered here -- _extract_date and
# _extract_time_range read them before they're stripped.
# ============================================================
_FILLERS = [
    "えっと", "えーっと", "えっとー", "えーと", "え〜と", "え〜っと",
    "あの", "あのー", "あのねー", "あー", "あーっと",
    "うー", "うーん", "んー", "んーっと", "んーと",
    "なんていうか", "なんていうの", "なんかさ", "なんかね",
    "なんだろう", "何だろう", "なんていうんだろう", "何て言うんだろう",
    "だから", "だからー", "だからさ", "だからね",
    "ねえ", "ねー", "ほら", "ほらね",
    "まあ", "まあね", "まあさ",
    "そうそう", "そういや", "そうそうそう",
    "いやー", "いやいや", "いやちょっと", "いや別に", "いやだから",
    "やっぱ", "やっぱり", "やっぱね", "やっぱさ",
    "じゃあ", "ちょっと", "多分", "ありがとう",
]

_TAIL_FILLERS = [
    "じゃん", "じゃんね", "じゃない", "じゃないかな", "じゃないか",
    "だよね", "だよ", "だね", "なんだよね", "なんだよ",
    "かな", "かなあ", "かなぁ", "かしら",
    "だっけ", "だっけな", "だっけかな", "っけ",
    "とか", "みたいな", "って感じ", "って感じかな",
    "いいかな", "からね", "んだけど", "んだけどもね",
]

# Stopwords: low-signal words plus the trigger words themselves
# ("remember", "recall", ...) so a "do you remember X" query doesn't
# turn "remember" into a search keyword. Time words are also stopped
# here since _extract_date / _extract_time_range read them separately.
_STOPWORDS = {
    "これ", "それ", "あれ", "どれ", "この", "その", "どの",
    "ここ", "そこ", "あそこ", "どこ",
    "ちょっと", "さっき", "今度", "今日", "昨日", "一昨日", "明日",
    "先週", "先月", "来週", "来月", "最近", "前回", "おととい",
    "たぶん", "きっと", "まあ", "なんか", "なんだか",
    "覚えてる", "覚えて", "覚えてるかい", "覚えていない",
    "憶えてる", "憶えて", "憶えてるかい",
    "言った", "言ってた", "言ってたじゃん", "話した", "話してた",
    "思い出", "思い出して", "記憶", "記録",
    "行った", "行ってた", "行きました", "やった", "やってた", "した", "してた",
    "一緒に", "について", "に関して",
    "一緒", "本当", "最初", "最後", "大体", "結局", "結果",
    "全部", "本気", "実際", "全然", "絶対", "普通", "感じ", "様子",
    "場合", "時間", "気分", "今回", "状態", "状況",
    "大丈夫", "ちゃんと", "一応", "まず", "とりあえず",
    "早速", "元々", "全く", "別に",
    "参照", "インスタンス", "形式", "テスト",
}

# English stopwords, checked per token only (never substring-replaced
# like the Japanese list above, so "do" can't eat into "document").
_EN_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "so", "of", "to", "in", "on",
    "at", "for", "with", "from", "by", "about", "around", "into", "over",
    "i", "me", "my", "we", "us", "our", "you", "your", "he", "she", "it",
    "its", "they", "them", "their", "this", "that", "these", "those",
    "is", "am", "are", "was", "were", "be", "been", "do", "does", "did",
    "have", "has", "had", "will", "would", "can", "could", "should",
    "what", "when", "where", "which", "who", "how", "why",
    "remember", "recall", "said", "say", "says", "told", "tell", "talk",
    "talked", "talking", "mention", "mentioned", "discuss", "discussed",
    "please", "just", "again", "then", "first", "previously", "ago", "last",
    "time", "thing", "things", "stuff",
    "yesterday", "today", "tonight", "morning", "afternoon", "evening", "night",
    "day", "days", "week", "weeks", "month", "months", "year", "years",
}


def _filters():
    """Return (fillers, tail_fillers, stopwords), from query_rules if
    available, else the built-in defaults above."""
    if _qr is not None:
        try:
            f = _qr.load_filters()
            return (f.get("fillers") or _FILLERS, f.get("tail_fillers") or _TAIL_FILLERS,
                    f.get("stopwords") or _STOPWORDS)
        except Exception:
            pass
    return _FILLERS, _TAIL_FILLERS, _STOPWORDS


def _strip_query(text):
    """Strip filler words and stopwords from a query, leaving the
    substance. Falls back to the original text if stripping empties
    it out."""
    if not text:
        return ""
    s = str(text)
    fillers, tail_fillers, stopwords = _filters()
    for f in sorted(fillers, key=len, reverse=True):
        s = s.replace(f, " ")
    for w in sorted(stopwords, key=len, reverse=True):
        s = s.replace(w, " ")
    changed = True
    while changed:
        changed = False
        stripped = s.rstrip("?？!！。、 　")
        for tf in tail_fillers:
            if stripped.endswith(tf):
                s = stripped[:-len(tf)].rstrip()
                changed = True
                break
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return "".join(str(text).split())
    return s


def _extract_keywords(text):
    """Pull out up to 5 meaningful chunks from a search query: runs of
    2+ katakana, 2+ kanji, kanji+okurigana, or alphanumeric tokens.
    Stopwords are excluded. If query_rules is available, also expands
    each keyword through its correction dictionary (e.g. mishearings)."""
    if not text:
        return []
    keywords = []
    seen = set()
    _, _tf, stopwords = _filters()
    _stop_lower = {s.lower() for s in stopwords}
    pattern = r"[ァ-ヶー]{2,}|[一-龥々]{2,}|[一-龥々][ぁ-ん]|[A-Za-z0-9][A-Za-z0-9_\-]+"
    # A single kanji followed by a particle ("日に", "何の") isn't a
    # useful keyword; a single kanji followed by an inflection ending
    # ("眠い", "見た") is, so only the particle case is dropped.
    _particles = set("にのをはがでともへやかねよさわ")
    for m in re.finditer(pattern, str(text)):
        kw = m.group(0)
        if len(kw) < 2:
            continue
        if len(kw) == 2 and "一" <= kw[0] <= "鿿" and kw[1] in _particles:
            continue
        if kw in stopwords or kw.lower() in _stop_lower or kw.lower() in _EN_STOPWORDS:
            continue
        if kw in seen:
            continue
        keywords.append(kw)
        seen.add(kw)
        if len(keywords) >= 5:
            break
    if _qr is not None:
        try:
            keywords = _qr.expand_keywords(keywords)
        except Exception:
            pass
    return keywords


# Markers of an "I don't have that" response. These are pushed to the
# back of the result order (never dropped) so a real hit outranks a
# hedge that happens to contain the same words.
_NEGATIVE_MARKERS = (
    "残っておりません", "残っていません", "残っておらず",
    "覚えておりません", "覚えていません", "覚えてはおりません",
    "ございません",
    "思い出せません", "思い出せず",
    "持っておりません", "持っていません",
    "現在のわたし", "今のわたし",
    "情報だけだと", "わかりませんでした", "分かりませんでした",
)


def _is_negative(text):
    if not text:
        return False
    return any(m in text for m in _NEGATIVE_MARKERS)


def _bigram(text):
    """Split whitespace-stripped text into 2-character windows, for
    FTS5 storage (Japanese has no word boundaries to tokenize on)."""
    if not text:
        return ""
    s = "".join(str(text).split())
    if len(s) <= 1:
        return s
    return " ".join(s[i:i + 2] for i in range(len(s) - 1))


def _bigram_query(text):
    """Same bigram split, joined with OR so any overlapping bigram
    counts as a partial match."""
    if not text:
        return ""
    s = "".join(str(text).split())
    if len(s) <= 1:
        return f'"{s}"' if s else ""
    grams = [s[i:i + 2] for i in range(len(s) - 1)]
    return " OR ".join(f'"{g}"' for g in grams)


def _keyword_and_query(keywords):
    """Multiple keywords joined with AND, e.g. ["work", "sleepy"] ->
    ("work") AND ("sleepy")."""
    groups = []
    for kw in keywords:
        bq = _bigram_query(kw)
        if bq:
            groups.append(f"({bq})")
    return " AND ".join(groups)


# ============================================================
# Temporal ruler: turns a spoken date/time-of-day into a UTC range
# comparable against the stored ts. Row ts is UTC; the words a user
# speaks ("today", "3 days ago", "at night") are in the user's timezone.
# ============================================================
def _parse_ts(ts_str):
    """Parse a stored ts string into an aware UTC datetime, or None."""
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(str(ts_str))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_UTC)
        return dt.astimezone(_UTC)
    except (ValueError, TypeError):
        return None


def _jst_day_range_utc(d):
    """The [00:00, 23:59:59.999999] window of date d in the user's timezone, as a (start,
    end) pair of aware UTC datetimes."""
    start_jst = datetime.combine(d, _time(0, 0, 0, 0), tzinfo=_tz())
    end_jst = datetime.combine(d, _time(23, 59, 59, 999999), tzinfo=_tz())
    return (start_jst.astimezone(_UTC), end_jst.astimezone(_UTC))


_RE_DATE_YMD = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_RE_DATE_MD_JP = re.compile(r"(\d{1,2})月(\d{1,2})日")
_RE_MONTH_JP = re.compile(r"(?<![\d年])(\d{1,2})月(?![\d\s]*日)")
_RE_DAYS_AGO = re.compile(r"(?<!\d)(\d{1,4})日前")
_RE_WEEKS_AGO = re.compile(r"(?<!\d)(\d{1,4})週間前")
_RE_YEARS_AGO = re.compile(r"(?<!\d)(\d{1,2})年前")
_RE_MONTHS_AGO = re.compile(r"(?<!\d)(\d{1,2})(?:ヶ|か|カ)月前")
_RE_DATE_MD = re.compile(r"(?<![\d:])(\d{1,2})/(\d{1,2})(?!\d)")
_DATE_REL_WORDS = ("一昨日", "おととい", "昨夜", "昨日", "今朝", "今夜", "今晩", "今日", "先々月", "先週", "先月", "一昨年", "去年")


def _year_range(y):
    return (_jst_day_range_utc(datetime(y, 1, 1).date())[0],
            _jst_day_range_utc(datetime(y, 12, 31).date())[1])


# ============================================================
# English time words. Same meanings as the Japanese rules above
# (whole words only, case-insensitive). A month name alone is read as
# a month only after "in"/"during" ("in July"), so the ordinary words
# "may" and "march" are never mistaken for dates.
# ============================================================
_EN_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4,
               "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
               "ten": 10, "eleven": 11, "twelve": 12}
_EN_NUM_RX = r"(\d{1,4}|an|a|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
_EN_MONTHS = {"january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
              "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
              "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9,
              "october": 10, "oct": 10, "november": 11, "nov": 11,
              "december": 12, "dec": 12}
_EN_MONTH_RX = (r"(january|february|march|april|may|june|july|august|september|"
                r"october|november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|"
                r"oct|nov|dec)\.?")
_RE_EN_MD = re.compile(r"\b" + _EN_MONTH_RX + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)
_RE_EN_DM = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + _EN_MONTH_RX + r"(?![a-z])", re.I)
_RE_EN_MONTH_ONLY = re.compile(r"\b(?:in|during)\s+" + _EN_MONTH_RX + r"(?![a-z])", re.I)
_RE_EN_AGO = re.compile(r"\b" + _EN_NUM_RX + r"\s+(day|week|month|year)s?\s+ago\b", re.I)
_EN_REL = (
    (re.compile(r"\b(?:the\s+)?day\s+before\s+yesterday\b", re.I), "day-2"),
    (re.compile(r"\byesterday(?:['’]s)?\b", re.I), "day-1"),
    (re.compile(r"\blast\s+night\b", re.I), "day-1"),
    (re.compile(r"\b(?:today(?:['’]s)?|tonight|this\s+(?:morning|afternoon|evening))\b", re.I), "day0"),
    (re.compile(r"\blast\s+week\b", re.I), "week-1"),
    (re.compile(r"\b(?:the\s+)?month\s+before\s+last\b", re.I), "month-2"),
    (re.compile(r"\blast\s+month\b", re.I), "month-1"),
    (re.compile(r"\b(?:the\s+)?year\s+before\s+last\b", re.I), "year-2"),
    (re.compile(r"\blast\s+year\b", re.I), "year-1"),
)


def _month_range(y, mo):
    """The whole calendar month mo of year y, as a UTC (start, end) pair."""
    first = datetime(y, mo, 1).date()
    nxt = datetime(y + 1, 1, 1).date() if mo == 12 else datetime(y, mo + 1, 1).date()
    return (_jst_day_range_utc(first)[0], _jst_day_range_utc(nxt - timedelta(days=1))[1])


def _months_back(today, n):
    """The calendar month n months before today's month."""
    y, mo = today.year, today.month - n
    while mo <= 0:
        mo += 12
        y -= 1
    return _month_range(y, mo)


def _extract_date_en(s, today):
    """The English half of _extract_date: returns a UTC (start, end)
    pair, or None. Meanings match the Japanese rules: a named day is
    that whole day, "N weeks ago" is that day +-3 days, "N months ago"
    / "last month" is that calendar month, "last year" is that
    calendar year, "last week" is the 7 days before today."""
    try:
        m = _RE_EN_MD.search(s)
        if m:
            try:
                return _jst_day_range_utc(datetime(today.year, _EN_MONTHS[m.group(1).lower()],
                                                   int(m.group(2))).date())
            except ValueError:
                pass
        m = _RE_EN_DM.search(s)
        if m:
            try:
                return _jst_day_range_utc(datetime(today.year, _EN_MONTHS[m.group(2).lower()],
                                                   int(m.group(1))).date())
            except ValueError:
                pass
        m = _RE_EN_MONTH_ONLY.search(s)
        if m:
            mo = _EN_MONTHS[m.group(1).lower()]
            y = today.year if mo <= today.month else today.year - 1
            return _month_range(y, mo)
        m = _RE_EN_AGO.search(s)
        if m:
            tok = m.group(1).lower()
            n = int(tok) if tok.isdigit() else _EN_NUMBERS[tok]
            unit = m.group(2).lower()
            if unit == "day":
                return _jst_day_range_utc(today - timedelta(days=n))
            if unit == "week":
                base = today - timedelta(weeks=n)
                return (_jst_day_range_utc(base - timedelta(days=3))[0],
                        _jst_day_range_utc(base + timedelta(days=3))[1])
            if unit == "month":
                return _months_back(today, n)
            return _year_range(today.year - n)
        for rx, key in _EN_REL:
            if not rx.search(s):
                continue
            if key == "day-2":
                return _jst_day_range_utc(today - timedelta(days=2))
            if key == "day-1":
                return _jst_day_range_utc(today - timedelta(days=1))
            if key == "day0":
                return _jst_day_range_utc(today)
            if key == "week-1":
                return (_jst_day_range_utc(today - timedelta(days=7))[0],
                        _jst_day_range_utc(today - timedelta(days=1))[1])
            if key == "month-2":
                return _months_back(today, 2)
            if key == "month-1":
                return _months_back(today, 1)
            if key == "year-2":
                return _year_range(today.year - 2)
            if key == "year-1":
                return _year_range(today.year - 1)
    except (ValueError, OverflowError):
        return None
    return None


def _extract_date(text):
    """Read a date out of the user's words (in the user's timezone) and
    return a UTC (start, end) datetime pair, or None. Recognizes explicit
    YYYY-MM-DD / YYYY/MM/DD / M/D dates (language neutral), Japanese
    month/day forms, and Japanese and English relative words (today,
    yesterday, N days/weeks/months/years ago, last week/month, ...)."""
    if not text:
        return None
    s = str(text)
    today = _today()

    m = _RE_DATE_YMD.search(s)
    if m:
        try:
            d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
            return _jst_day_range_utc(d)
        except ValueError:
            pass

    m = _RE_DATE_MD_JP.search(s)
    if m:
        try:
            d = datetime(today.year, int(m.group(1)), int(m.group(2))).date()
            return _jst_day_range_utc(d)
        except ValueError:
            pass

    # "July" alone -> that whole month, this year (or last year if the
    # named month hasn't happened yet this year).
    m = _RE_MONTH_JP.search(s)
    if m:
        mo = int(m.group(1))
        if 1 <= mo <= 12:
            y = today.year if mo <= today.month else today.year - 1
            first = datetime(y, mo, 1).date()
            nxt = datetime(y + 1, 1, 1).date() if mo == 12 else datetime(y, mo + 1, 1).date()
            return (_jst_day_range_utc(first)[0], _jst_day_range_utc(nxt - timedelta(days=1))[1])

    m = _RE_DAYS_AGO.search(s)
    if m:
        d = today - timedelta(days=int(m.group(1)))
        return _jst_day_range_utc(d)

    m = _RE_WEEKS_AGO.search(s)
    if m:
        base = today - timedelta(weeks=int(m.group(1)))
        start = _jst_day_range_utc(base - timedelta(days=3))[0]
        end = _jst_day_range_utc(base + timedelta(days=3))[1]
        return (start, end)

    m = _RE_YEARS_AGO.search(s)
    if m:
        try:
            return _year_range(today.year - int(m.group(1)))
        except ValueError:
            pass

    m = _RE_MONTHS_AGO.search(s)
    if m:
        y, mo = today.year, today.month - int(m.group(1))
        while mo <= 0:
            mo += 12
            y -= 1
        try:
            first = datetime(y, mo, 1).date()
            nxt = datetime(y + 1, 1, 1).date() if mo == 12 else datetime(y, mo + 1, 1).date()
            return (_jst_day_range_utc(first)[0], _jst_day_range_utc(nxt - timedelta(days=1))[1])
        except ValueError:
            pass

    if "一昨日" in s or "おととい" in s:
        return _jst_day_range_utc(today - timedelta(days=2))
    if "昨夜" in s:  # last night = yesterday (time-of-day handled by _extract_time_range)
        return _jst_day_range_utc(today - timedelta(days=1))
    if "今朝" in s or "今夜" in s or "今晩" in s:  # this morning/tonight = today
        return _jst_day_range_utc(today)
    if "昨日" in s:
        return _jst_day_range_utc(today - timedelta(days=1))
    if "今日" in s:
        return _jst_day_range_utc(today)
    if "先週" in s:
        start = _jst_day_range_utc(today - timedelta(days=7))[0]
        end = _jst_day_range_utc(today - timedelta(days=1))[1]
        return (start, end)
    if "先々月" in s:
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        first_prev = last_prev.replace(day=1)
        last_prev2 = first_prev - timedelta(days=1)
        return (_jst_day_range_utc(last_prev2.replace(day=1))[0], _jst_day_range_utc(last_prev2)[1])
    if "先月" in s:
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        first_prev = last_prev.replace(day=1)
        start = _jst_day_range_utc(first_prev)[0]
        end = _jst_day_range_utc(last_prev)[1]
        return (start, end)
    if "一昨年" in s:
        return _year_range(today.year - 2)
    if "去年" in s:
        return _year_range(today.year - 1)

    r = _extract_date_en(s, today)
    if r is not None:
        return r

    m = _RE_DATE_MD.search(s)
    if m:
        try:
            d = datetime(today.year, int(m.group(1)), int(m.group(2))).date()
            return _jst_day_range_utc(d)
        except ValueError:
            pass

    return None


# Named time-of-day windows (user's timezone). End is exclusive.
_TIME_WORDS = [
    ("深夜", (0, 5)),
    ("早朝", (4, 7)),
    ("明け方", (3, 6)),
    ("朝", (5, 11)),
    ("午前", (5, 12)),
    ("昼間", (9, 17)),
    ("昼", (11, 14)),
    ("正午", (11, 13)),
    ("午後", (12, 18)),
    ("夕方", (16, 19)),
    ("夕", (16, 19)),
    ("夜中", (22, 24)),
    ("夜", (18, 24)),
    ("晩", (18, 23)),
]

# Words that look like a time-of-day mention but aren't (a duration,
# or a fixed compound like "dinner"/"all-nighter"). Stripped before
# matching so they don't get misread as a time filter.
_TIME_FALSE_FRIENDS = re.compile(
    r"(夕食|夕飯|夕張|朝食|朝飯|朝礼|昼食|昼飯|夜食|夜勤|徹夜|一昼夜)")

# English time-of-day words (checked longest first; end is exclusive).
_EN_TIME_WORDS = [
    ("late at night", (22, 24)),
    ("late night", (22, 24)),
    ("early morning", (4, 7)),
    ("morning", (5, 11)),
    ("midday", (11, 14)),
    ("lunchtime", (11, 14)),
    ("noon", (11, 13)),
    ("afternoon", (12, 18)),
    ("evening", (17, 21)),
    ("tonight", (18, 24)),
    ("night", (18, 24)),
]
_EN_TIME_FALSE_FRIENDS = re.compile(
    r"\bgood\s*(?:morning|afternoon|evening|night)\b|\bgoodnight\b|\bovernight\b", re.I)
_RE_EN_CLOCK = re.compile(
    r"\b(?:(around|about|at)\s+)?(\d{1,2})(?::\d{2})?\s*(a\.?m\.?|p\.?m\.?)(?![a-z])", re.I)


def _extract_time_range_en(s):
    """The English half of _extract_time_range: (start_hour, end_hour)
    in the user's timezone, or None."""
    s = _EN_TIME_FALSE_FRIENDS.sub(" ", s)
    m = _RE_EN_CLOCK.search(s)
    if m:
        h = int(m.group(2))
        if 1 <= h <= 12:
            h = (h % 12) + (12 if m.group(3).lower().startswith("p") else 0)
            if m.group(1) and m.group(1).lower() in ("around", "about"):
                return (max(0, h - 1), min(24, h + 2))
            return (h, min(24, h + 1))
    for word, rng in sorted(_EN_TIME_WORDS, key=lambda x: len(x[0]), reverse=True):
        if re.search(r"\b" + word.replace(" ", r"\s+") + r"\b", s, re.I):
            return rng
    return None


def _mask_en_time(text, time_read):
    """Blank out the English date phrases (and, if a time of day was
    read, the time-of-day phrases) so "yesterday" or "3 days ago" is
    not also searched for as a keyword. Mirrors how _split_time_query
    blanks the Japanese words."""
    s = str(text or "")
    for rx in (_RE_EN_MD, _RE_EN_DM, _RE_EN_MONTH_ONLY, _RE_EN_AGO):
        s = rx.sub(" ", s)
    for rx, _key in _EN_REL:
        s = rx.sub(" ", s)
    if time_read:
        s = _RE_EN_CLOCK.sub(" ", s)
        for word, _rng in sorted(_EN_TIME_WORDS, key=lambda x: len(x[0]), reverse=True):
            s = re.sub(r"\b" + word.replace(" ", r"\s+") + r"\b", " ", s, flags=re.I)
    return s


def _extract_time_range(text):
    """Read a time-of-day out of the user's words (user's timezone) and return
    (start_hour, end_hour), or None."""
    if not text:
        return None
    s = _TIME_FALSE_FRIENDS.sub(" ", str(text))

    m = re.search(r"(\d{1,2})時頃", s)
    if m:
        h = int(m.group(1)) % 24
        return (max(0, h - 1), min(24, h + 2))

    m = re.search(r"(\d{1,2})時(?!間)", s)  # "N時間" is a duration, not a time
    if m:
        h = int(m.group(1)) % 24
        return (h, min(24, h + 1))

    for word, rng in sorted(_TIME_WORDS, key=lambda x: len(x[0]), reverse=True):
        if word in s:
            return rng
    r = _extract_time_range_en(s)
    if r is not None:
        return r
    return None


def _in_date_range(ts_dt, date_range):
    if date_range is None:
        return True
    if ts_dt is None:
        return False
    start, end = date_range
    return start <= ts_dt <= end


def _in_time_range(ts_dt, time_jst):
    if time_jst is None:
        return True
    if ts_dt is None:
        return False
    h = ts_dt.astimezone(_tz()).hour
    start, end = time_jst
    return start <= h < end


def _src_key(fp):
    """Tag rows with where they came from, e.g. "main/2026-08-29" or
    "corpus/notes/2026-01-26"."""
    base = os.path.dirname(_log_main())
    rel = os.path.relpath(fp, base).replace("\\", "/")
    return rel[:-6] if rel.endswith(".jsonl") else rel


def _insert_rows(con, table, src, lines):
    n = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue  # a corrupted line is skipped, not fixed -- source logs are read-only
        con.execute(
            "INSERT INTO %s"
            "(bigram, ts, actor, role, type, text, model, session, src)"
            " VALUES (?,?,?,?,?,?,?,?,?)" % table,
            (
                _bigram(r.get("text", "")),
                r.get("ts"), r.get("actor"), r.get("role"),
                r.get("type"), r.get("text"), r.get("model"), r.get("session"),
                src,
            ),
        )
        n += 1
    return n


def _tail_sig(lines, k):
    """Fingerprint of line k (used to detect a rewritten file before
    trusting an append)."""
    if k <= 0 or k > len(lines):
        return ""
    return hashlib.sha1(lines[k - 1].encode("utf-8", "replace")).hexdigest()[:16]


def build_index(force=False):
    """(Re)build the FTS5 index from <data_dir>/main and
    <data_dir>/corpus/*/*.jsonl.

    Normally does an incremental append: each source file's previous
    line count is tracked in src_progress, and only new lines are
    inserted. A file whose line count shrank, or whose last-seen line
    changed, is fully re-indexed. force=True (or no usable existing
    index) triggers a full rebuild, built into a side table and then
    swapped in atomically so readers never see an empty index while
    it's being rebuilt.

    Returns the index's total row count."""
    index_db = _index_db()
    os.makedirs(os.path.dirname(index_db), exist_ok=True)
    con = sqlite3.connect(index_db)
    try:
        con.execute("PRAGMA journal_mode=WAL")  # don't block readers while writing
        files = sorted(glob.glob(os.path.join(_log_main(), "*.jsonl")))
        files += sorted(glob.glob(os.path.join(_log_corpus(), "*", "*.jsonl")))
        try:
            cols = [r[1] for r in con.execute("PRAGMA table_info(recall)")]
        except sqlite3.Error:
            cols = []
        full = force or ("src" not in cols)
        ddl = ("CREATE VIRTUAL TABLE %s USING fts5("
               "  bigram,"
               "  ts UNINDEXED, actor UNINDEXED, role UNINDEXED,"
               "  type UNINDEXED, text UNINDEXED, model UNINDEXED, session UNINDEXED,"
               "  src UNINDEXED,"
               "  tokenize='unicode61'"
               ")")
        con.execute("CREATE TABLE IF NOT EXISTS src_progress"
                    "(src TEXT PRIMARY KEY, nlines INTEGER, sig TEXT)")
        if full:
            con.execute("DROP TABLE IF EXISTS recall_new")
            con.execute(ddl % "recall_new")
            prog = {}
            for fp in files:
                src = _src_key(fp)
                with open(fp, encoding="utf-8") as f:
                    lines = f.readlines()
                _insert_rows(con, "recall_new", src, lines)
                prog[src] = (len(lines), _tail_sig(lines, len(lines)))
            con.commit()
            con.isolation_level = None
            con.execute("BEGIN IMMEDIATE")
            con.execute("DROP TABLE IF EXISTS recall")
            con.execute("ALTER TABLE recall_new RENAME TO recall")
            con.execute("DELETE FROM src_progress")
            con.executemany("INSERT INTO src_progress(src, nlines, sig) VALUES (?,?,?)",
                            [(k, v[0], v[1]) for k, v in prog.items()])
            con.execute("COMMIT")
            con.isolation_level = ""
        else:
            prog = {r[0]: (r[1], r[2]) for r in con.execute("SELECT src, nlines, sig FROM src_progress")}
            seen = set()
            for fp in files:
                src = _src_key(fp)
                seen.add(src)
                with open(fp, encoding="utf-8") as f:
                    lines = f.readlines()
                cur = len(lines)
                done, sig = prog.get(src, (0, ""))
                if cur == done and _tail_sig(lines, cur) == sig:
                    continue  # unchanged
                if cur < done or _tail_sig(lines, done) != sig:
                    con.execute("DELETE FROM recall WHERE src = ?", (src,))  # file was rewritten
                    done = 0
                _insert_rows(con, "recall", src, lines[done:])
                con.execute("INSERT OR REPLACE INTO src_progress(src, nlines, sig) VALUES (?,?,?)",
                            (src, cur, _tail_sig(lines, cur)))
            for src in list(prog):
                if src not in seen:  # source file disappeared -- drop its rows, not the logs
                    con.execute("DELETE FROM recall WHERE src = ?", (src,))
                    con.execute("DELETE FROM src_progress WHERE src = ?", (src,))
            con.commit()
        return con.execute("SELECT count(*) FROM recall").fetchone()[0]
    finally:
        con.close()


def _fetch_rows(con, match_query, actor, hard_limit):
    if match_query:
        sql = ("SELECT rowid, ts, actor, role, type, text, model, session"
               " FROM recall WHERE bigram MATCH ? AND type != 'meta'")
        params = [match_query]
        if actor:
            sql += " AND actor = ?"
            params.append(actor)
        sql += " ORDER BY rank LIMIT ?"
        params.append(int(hard_limit))
    else:
        # no keyword (date/time-of-day only): take everything, newest
        # first, and let the time filter below narrow it down.
        sql = ("SELECT rowid, ts, actor, role, type, text, model, session"
               " FROM recall")
        params = []
        if actor:
            sql += " WHERE actor = ? AND type != 'meta'"
            params.append(actor)
        else:
            sql += " WHERE type != 'meta'"
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(int(hard_limit))
    return [dict(r) for r in con.execute(sql, params)]


def _apply_time_filters(rows, date_range, time_jst):
    """Filter rows by date and/or time-of-day (user's timezone). Whichever filter
    is None passes everything through."""
    if date_range is None and time_jst is None:
        return rows
    out = []
    for r in rows:
        ts_dt = _parse_ts(r.get("ts"))
        if _in_date_range(ts_dt, date_range) and _in_time_range(ts_dt, time_jst):
            out.append(r)
    return out


def _fetch_day(con, date_range, time_jst, actor, limit):
    """Date mode: return the whole day (or time-of-day window within
    it) in chronological order, for a "what did we talk about on day
    X" query -- the whole flow, not a scattering of fragments. If too
    many rows match, keep the most recent `limit` (chronological order
    is preserved)."""
    sql = "SELECT ts, actor, role, type, text, model, session FROM recall"
    params = []
    if actor:
        sql += " WHERE actor = ? AND type != 'meta'"
        params.append(actor)
    else:
        sql += " WHERE type != 'meta'"
    sql += " ORDER BY ts ASC"
    rows = [dict(r) for r in con.execute(sql, params)]
    rows = _apply_time_filters(rows, date_range, time_jst)
    if len(rows) > limit:
        rows = rows[-limit:]
    return rows


def search(keyword, actor=None, limit=5):
    """Search by keyword and/or date and/or time-of-day, newest first.
    All three are optional and combine freely -- with nothing at all
    it just returns the most recent rows. Returns a list of raw-row
    dicts (the 7 core fields). Builds the index automatically if it
    doesn't exist yet."""
    if not os.path.exists(_index_db()):
        build_index()

    con = sqlite3.connect(_index_db())
    con.row_factory = sqlite3.Row
    try:
        date_range = _extract_date(keyword)        # read from the raw text, before stripping
        time_jst = _extract_time_range(keyword)
        cleaned = _strip_query(_mask_en_time(keyword, time_jst is not None))  # strip time words and filler words
        keywords = _extract_keywords(cleaned)

        # date/time filters can remove rows, so over-fetch before trimming to limit
        hard_limit = int(limit) * 8 + 20

        def _run(match_query):
            rows = _fetch_rows(con, match_query, actor, hard_limit)
            rows = _apply_time_filters(rows, date_range, time_jst)
            rows.sort(key=lambda r: _is_negative(r.get("text", "")))  # negatives last (stable sort)
            for r in rows:
                r.pop("rowid", None)
            return rows[:int(limit)]

        if len(keywords) >= 2:
            rows = _run(_keyword_and_query(keywords))
            if rows:
                return rows
        if cleaned.strip():
            rows = _run(_bigram_query(cleaned))
            if rows:
                return rows
        return _run("")
    finally:
        con.close()


def search_with_context(keyword, actor=None, hits=2, around=1, day_limit=40):
    """Like search(), but also returns `around` rows before and after
    each hit, from the same session, in chronological order with no
    duplicates."""
    if not os.path.exists(_index_db()):
        build_index()
    con = sqlite3.connect(_index_db())
    con.row_factory = sqlite3.Row
    try:
        date_range = _extract_date(keyword)
        time_jst = _extract_time_range(keyword)
        cleaned = _strip_query(_mask_en_time(keyword, time_jst is not None))
        keywords = _extract_keywords(cleaned)

        # Date mode with no other keywords ("what did we talk about on
        # July 4th") means "show me the whole day", not "find a
        # fragment" -- use _fetch_day instead of a hit + context pull.
        if date_range is not None and not keywords:
            return _fetch_day(con, date_range, time_jst, actor, day_limit)

        hard_limit = int(hits) * 8 + 20

        def _candidates(match_query):
            rows = _fetch_rows(con, match_query, actor, hard_limit)
            rows = _apply_time_filters(rows, date_range, time_jst)
            return rows

        cand = []
        if len(keywords) >= 2:
            cand = _candidates(_keyword_and_query(keywords))
        if not cand and cleaned.strip():
            cand = _candidates(_bigram_query(cleaned))
        if not cand:
            cand = _candidates("")
        if not cand:
            return []

        cand.sort(key=lambda r: _is_negative(r.get("text", "")))
        hit_sel = cand[:int(hits)]

        rowid_session = {r["rowid"]: r["session"]
                         for r in con.execute("SELECT rowid, session FROM recall")}
        wanted = set()
        for h in hit_sel:
            rid = h["rowid"]
            sess = h.get("session")
            wanted.add(rid)
            for i in range(rid - around, rid + around + 1):
                if i >= 1 and rowid_session.get(i) == sess:
                    wanted.add(i)
        if not wanted:
            return []
        placeholders = ",".join("?" * len(wanted))
        rows = con.execute(
            "SELECT rowid, ts, actor, role, type, text, model, session"
            f" FROM recall WHERE rowid IN ({placeholders}) ORDER BY rowid ASC",
            sorted(wanted),
        )
        result = []
        for r in rows:
            d = dict(r)
            d.pop("rowid", None)
            result.append(d)
        # Context rows are pulled by adjacent rowid within the same
        # session, so a hit near a date boundary can pull in the
        # previous/next day. If a date/time filter was given, re-apply
        # it to the context rows too.
        if date_range is not None or time_jst is not None:
            result = _apply_time_filters(result, date_range, time_jst)
        return result
    finally:
        con.close()


def _split_time_query(query):
    """The single source of truth for splitting a query into
    (date_range, time_jst, keywords). Callers must not re-parse dates
    themselves -- two independent regexes drifting apart is how a
    keyword like the day-of-month in "July 19th" ends up re-matched as
    a search term.

    - date_range: from _extract_date (UTC start/end pair) or None
    - time_jst:   from _extract_time_range (hour range, user's timezone) or None
    - keywords:   the remaining search terms with all time words
                  removed (max 5, per _extract_keywords)
    """
    s = str(query or "")
    date_range = _extract_date(s)
    time_jst = _extract_time_range(s)

    masked = s
    for rx in (_RE_DATE_YMD, _RE_DATE_MD_JP, _RE_DAYS_AGO, _RE_WEEKS_AGO,
               _RE_YEARS_AGO, _RE_MONTHS_AGO, _RE_DATE_MD, _RE_MONTH_JP):
        masked = rx.sub(" ", masked)
    for w in _DATE_REL_WORDS:
        masked = masked.replace(w, " ")
    if time_jst is not None:
        # protect fixed compounds (e.g. "dinner") before stripping time words
        prot = {}
        for i, m in enumerate(_TIME_FALSE_FRIENDS.finditer(masked)):
            tok = "%d" % i
            prot[tok] = m.group(0)
        for tok, w in prot.items():
            masked = masked.replace(w, tok, 1)
        masked = re.sub(r"(\d{1,2})時(頃|台|ごろ)?(?!間)", " ", masked)
        for word, _rng in sorted(_TIME_WORDS, key=lambda x: len(x[0]), reverse=True):
            masked = masked.replace(word, " ")
        for tok, w in prot.items():
            masked = masked.replace(tok, w)
    masked = _mask_en_time(masked, time_jst is not None)
    keywords = _extract_keywords(_strip_query(masked)) if masked.strip() else []
    # A temporal modifier ("the first time", "back then") means "which
    # occurrence", not "which date" -- widen date_range to everything.
    if _qr is not None:
        try:
            if _qr.has_temporal_modifier(s):
                date_range = None
        except Exception:
            pass
    return date_range, time_jst, keywords


def _latest_day_with_hours(time_jst):
    """When only a time-of-day was given (no date), find the most
    recent day (user's timezone) that has rows in that window and return its
    (start, end) in UTC. None if nothing matches."""
    if not os.path.exists(_index_db()):
        build_index()
    con = sqlite3.connect(_index_db())
    try:
        for (ts,) in con.execute("SELECT ts FROM recall WHERE type != 'meta' ORDER BY ts DESC"):
            t = _parse_ts(ts)
            if t is not None and _in_time_range(t, time_jst):
                return _jst_day_range_utc(t.astimezone(_tz()).date())
        return None
    finally:
        con.close()


def search_time(date_range, time_jst, keywords, actor=None,
                hits=8, around=0, day_limit=40):
    """The formal entry point for time-scoped search: takes an
    already-parsed (date_range, time_jst, keywords) -- typically from
    _split_time_query -- and returns only what's inside that range.
    Never re-parses the original text.

    Returns (rows, meta) where rows is chronological (oldest first)
    and meta carries: total (row count in range before keyword
    filtering, for an honest "how much is really there"), kw_total
    (row count after keyword filtering, or None in date-only mode),
    days (per-day counts), types (per-type counts), level ("L1" =
    keyword filter applied and kept, "L2" = keyword filter dropped
    for being too narrow), and fallback_day (set when no date was
    given and a time-of-day fell back to the most recent matching
    day)."""
    fallback_day = None
    if date_range is None and time_jst is not None:
        date_range = _latest_day_with_hours(time_jst)
        if date_range is not None:
            fallback_day = date_range[0].astimezone(_tz()).strftime("%Y-%m-%d")
    if date_range is None:
        return [], {"total": 0, "kw_total": None, "days": {}, "types": {},
                     "level": None, "fallback_day": fallback_day}
    if not os.path.exists(_index_db()):
        build_index()
    con = sqlite3.connect(_index_db())
    con.row_factory = sqlite3.Row
    try:
        # Coarse filter in SQL first (ts is stored as a sortable ISO
        # string), then apply the exact time-of-day filter in Python.
        # A pure-Python scan of every row was measurably too slow once
        # the log grew large.
        _lo = (date_range[0] - timedelta(seconds=1)).isoformat()
        _hi = (date_range[1] + timedelta(seconds=1)).isoformat()
        _sql = ("SELECT rowid, ts, actor, role, type, text, model, session"
                " FROM recall WHERE ts >= ? AND ts <= ? AND type != 'meta' AND type != 'doc'")
        _params = [_lo, _hi]
        if actor:
            _sql += " AND actor = ?"
            _params.append(actor)
        all_rows = [dict(r) for r in con.execute(_sql, _params)]
        in_range = _apply_time_filters(all_rows, date_range, time_jst)
        total = len(in_range)

        def _day_counts(rows):
            d = {}
            for r in rows:
                t = _parse_ts(r.get("ts"))
                if t is not None:
                    k = t.astimezone(_tz()).strftime("%m-%d")
                    d[k] = d.get(k, 0) + 1
            return d

        def _type_counts(rows):
            d = {}
            for r in rows:
                k = r.get("type") or "text"
                d[k] = d.get(k, 0) + 1
            return d

        if not keywords:
            rows = sorted(in_range, key=lambda r: (r.get("ts") or ""))
            meta = {"total": total, "kw_total": None, "days": _day_counts(in_range),
                    "types": _type_counts(in_range), "level": None, "fallback_day": fallback_day}
            for r in rows:
                r.pop("rowid", None)
            return rows, meta

        # With keywords: narrow the range first, then search inside it.
        # Ranking the whole table first and *then* clipping to the
        # range can drop a real match that just didn't rank highly
        # globally; the in-range set is small enough to scan directly.
        cand = [r for r in in_range
                if all(k.lower() in (r.get("text") or "").lower() for k in keywords)]
        level = "L1"
        if len(cand) < 2:
            cand = list(in_range)
            level = "L2"
        if not cand:
            return [], {"total": total, "kw_total": 0, "days": {}, "types": {},
                         "level": level, "fallback_day": fallback_day}

        cand.sort(key=lambda r: _is_negative(r.get("text", "")))
        hit_sel = cand

        if int(around) > 0:
            rowid_session = {r["rowid"]: r["session"]
                             for r in con.execute("SELECT rowid, session FROM recall")}
            wanted = set()
            for h in hit_sel:
                rid = h["rowid"]
                sess = h.get("session")
                wanted.add(rid)
                for i in range(rid - int(around), rid + int(around) + 1):
                    if i >= 1 and rowid_session.get(i) == sess:
                        wanted.add(i)
            placeholders = ",".join("?" * len(wanted))
            rows = [dict(r) for r in con.execute(
                "SELECT rowid, ts, actor, role, type, text, model, session"
                f" FROM recall WHERE rowid IN ({placeholders}) ORDER BY rowid ASC",
                sorted(wanted))]
            rows = _apply_time_filters(rows, date_range, time_jst)
        else:
            rows = hit_sel

        rows = sorted(rows, key=lambda r: (r.get("ts") or ""))
        meta = {"total": total, "kw_total": len(cand), "days": _day_counts(cand),
                "types": _type_counts(cand), "level": level, "fallback_day": fallback_day}
        for r in rows:
            r.pop("rowid", None)
        return rows, meta
    finally:
        con.close()


if __name__ == "__main__":
    import sys
    print("=== index_exact self-test ===")
    n = build_index(force=("--force" in sys.argv))
    print(f"index built: {n} rows")

    print("\n--- keyword search ---")
    for kw in ("memory", "budget"):
        hits = search(kw, limit=3)
        print(f"search '{kw}' -> {len(hits)} hit(s)")

    print("\n--- date-scoped search demo ---")
    for kw in ("昨日のログ", "今日の話", "yesterday's log", "what we said today"):
        hits = search(kw, limit=3)
        print(f"search '{kw}' -> {len(hits)} hit(s)")

    print("\n--- with context ---")
    ctx = search_with_context("memory", hits=1, around=1)
    print(f"hit + context -> {len(ctx)} row(s)")
