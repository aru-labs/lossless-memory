# -*- coding: utf-8 -*-
"""Query pre-processing rules: filler/stopword dictionaries, temporal
modifiers, ASR-style correction expansion, and compaction-boundary
lookup.

Kept as one small file, separate from index_exact.py, so the search
engine module doesn't grow into a dumping ground for every heuristic.
index_exact.py, index_vector.py and recall.py must not import each
other's private helpers through here (no circular imports) -- every
public function in this module is wrapped in try/except so a missing
or broken settings file never breaks a search.
"""
import os
import json
import glob
import time

from .config import config as _base_config, data_dir

_CACHE_TTL = 30  # seconds, shared by every settings file below

_cache = {}  # path -> (loaded_at, data)


def _settings_path(name):
    return os.path.join(data_dir(), name)


def _load_json_cached(path):
    """Read JSON from path with a 30s cache. Returns None on any failure
    (missing file, bad JSON) instead of raising."""
    now = time.time()
    hit = _cache.get(path)
    if hit is not None and (now - hit[0]) < _CACHE_TTL:
        return hit[1]
    data = None
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
    except Exception:
        data = None
    _cache[path] = (now, data)
    return data


def config():
    """Thin re-export so callers that only need settings don't have to
    import config.py directly."""
    return _base_config()


# ============================================================
# Temporal modifiers ("last time", "the first one", ...): when a query
# contains one of these, the caller should widen the date range instead
# of narrowing it -- the user means "which occurrence", not "which day".
# ============================================================
_TEMPORAL_MODIFIERS_BUILTIN = [
    "1回目", "2回目", "3回目", "4回目", "5回目", "何回目",
    "一回目", "二回目", "三回目", "四回目", "五回目",
    "1度目", "2度目", "3度目",
    "一度目", "二度目", "三度目",
    "最初の", "最初に", "最初は",
    "最後の", "最後に", "最後は",
    "途中で", "途中の",
    "あの時", "あのとき", "その時", "そのとき",
    "いつだったか", "いつだっけ",
    "前回", "前々回", "次回",
    "以前", "以前は",
]

_TRIGGERS_FILE = "recall_triggers.json"


def _temporal_modifiers():
    try:
        data = _load_json_cached(_settings_path(_TRIGGERS_FILE))
        if isinstance(data, dict) and isinstance(data.get("temporal_modifiers"), list):
            words = [w for w in data["temporal_modifiers"] if isinstance(w, str) and w.strip()]
            if words:
                return words
    except Exception:
        pass
    return _TEMPORAL_MODIFIERS_BUILTIN


def has_temporal_modifier(text):
    """True if the query names an occurrence ("the first time", ...)
    rather than a date -- callers should widen date_range to None."""
    try:
        if not text:
            return False
        return any(marker in text for marker in _temporal_modifiers())
    except Exception:
        return False


# ============================================================
# Optional correction-dictionary expansion (e.g. speech-to-text
# mishearings). Off by default -- only active if config's
# asr_corrections points at a real file.
# ============================================================
_asr_cache = {}  # path -> (loaded_at, dict)


def load_corrections():
    """Return the {wrong: right} dict from config's asr_corrections path.
    Empty dict if unset, missing, or broken -- this feature is silently
    optional."""
    try:
        path = config().get("asr_corrections")
        if not path:
            return {}
        now = time.time()
        hit = _asr_cache.get(path)
        if hit is not None and (now - hit[0]) < _CACHE_TTL:
            return hit[1]
        data = {}
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict) and isinstance(raw.get("corrections"), dict):
                    data = raw["corrections"]
                elif isinstance(raw, dict):
                    data = {k: v for k, v in raw.items() if isinstance(v, str)}
        except Exception:
            data = {}
        _asr_cache[path] = (now, data)
        return data
    except Exception:
        return {}


def expand_keywords(keywords):
    """Add both the mistaken and corrected spelling of each keyword that
    matches a configured correction, so a search catches either form."""
    try:
        corrections = load_corrections()
        if not corrections:
            return keywords
        expanded = list(keywords)
        seen = set(keywords)
        for kw in keywords:
            if kw in corrections:
                corrected = corrections[kw]
                if corrected and corrected not in seen:
                    expanded.append(corrected)
                    seen.add(corrected)
            for wrong, correct in corrections.items():
                if wrong != kw and wrong in kw:
                    replaced = kw.replace(wrong, correct)
                    if replaced and replaced not in seen:
                        expanded.append(replaced)
                        seen.add(replaced)
        return expanded
    except Exception:
        return keywords


# ============================================================
# Filler / stopword / protected-name dictionaries. Built-in defaults
# cover common Japanese conversational filler; a settings file
# (rag_filters.json in data_dir) can add to or subtract from them
# without editing code.
# ============================================================
_FILLERS_BUILTIN = [
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

_TAIL_FILLERS_BUILTIN = [
    "じゃん", "じゃんね", "じゃない", "じゃないかな", "じゃないか",
    "だよね", "だよ", "だね", "なんだよね", "なんだよ",
    "かな", "かなあ", "かなぁ", "かしら",
    "だっけ", "だっけな", "だっけかな", "っけ",
    "とか", "みたいな", "って感じ", "って感じかな",
    "いいかな", "からね", "んだけど", "んだけどもね",
]

_STOPWORDS_BUILTIN = {
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

_PROTECTED_NAMES_BUILTIN = set()  # extend via config's protected_names / rag_filters.json

_FILTERS_FILE = "rag_filters.json"


def load_filters():
    """Return fillers/tail_fillers/stopwords/protected_names, merging
    built-in defaults with data_dir/rag_filters.json (if present)."""
    try:
        fillers = list(_FILLERS_BUILTIN)
        tail_fillers = list(_TAIL_FILLERS_BUILTIN)
        stopwords = set(_STOPWORDS_BUILTIN)
        protected_names = set(_PROTECTED_NAMES_BUILTIN)
        try:
            cfg = config()
            for name in cfg.get("protected_names") or []:
                if isinstance(name, str) and name.strip():
                    protected_names.add(name)
        except Exception:
            pass

        cfg_file = _load_json_cached(_settings_path(_FILTERS_FILE))
        if isinstance(cfg_file, dict):
            fcfg = cfg_file.get("fillers", {}) or {}
            for w in fcfg.get("include", []) or []:
                if isinstance(w, str) and w.strip() and w not in fillers:
                    fillers.append(w)
            for w in fcfg.get("exclude", []) or []:
                if isinstance(w, str) and w in fillers:
                    fillers.remove(w)

            tcfg = cfg_file.get("tail_fillers", {}) or {}
            for w in tcfg.get("include", []) or []:
                if isinstance(w, str) and w.strip() and w not in tail_fillers:
                    tail_fillers.append(w)
            for w in tcfg.get("exclude", []) or []:
                if isinstance(w, str) and w in tail_fillers:
                    tail_fillers.remove(w)

            scfg = cfg_file.get("stopwords", {}) or {}
            for w in scfg.get("include", []) or []:
                if isinstance(w, str) and w.strip():
                    stopwords.add(w)
            for w in scfg.get("exclude", []) or []:
                if isinstance(w, str):
                    stopwords.discard(w)

            pcfg = cfg_file.get("protected_names", {}) or {}
            for w in pcfg.get("include", []) or []:
                if isinstance(w, str) and w.strip():
                    protected_names.add(w)
            for w in pcfg.get("exclude", []) or []:
                if isinstance(w, str):
                    protected_names.discard(w)

        return {
            "fillers": fillers,
            "tail_fillers": tail_fillers,
            "stopwords": stopwords,
            "protected_names": protected_names,
        }
    except Exception:
        return {
            "fillers": list(_FILLERS_BUILTIN),
            "tail_fillers": list(_TAIL_FILLERS_BUILTIN),
            "stopwords": set(_STOPWORDS_BUILTIN),
            "protected_names": set(_PROTECTED_NAMES_BUILTIN),
        }


# ============================================================
# Trigger words that should make a caller run a recall search at all
# (recall_triggers.json in data_dir can add to or subtract from these).
# ============================================================
_TRIGGERS_BUILTIN_INCLUDE = [
    "覚えてるかい", "覚えてないかい", "記憶にないのかい", "思い出して", "憶えて",
    "言ってた", "本ないかい", "本読んで", "説明書ないかい", "説明書読んで", "圧縮前",
    "remember", "recall", "what did I say",
]
_COMPACT_WORDS_BUILTIN = ["圧縮前", "N回前の圧縮", "前々回", "前々前回"]


def load_triggers():
    """Return include/exclude/temporal_modifiers/compact_words."""
    try:
        include = list(_TRIGGERS_BUILTIN_INCLUDE)
        exclude = []
        data = _load_json_cached(_settings_path(_TRIGGERS_FILE))
        if isinstance(data, dict):
            for w in data.get("include", []) or []:
                if isinstance(w, str) and w.strip() and w not in include:
                    include.append(w)
            for w in data.get("exclude", []) or []:
                if isinstance(w, str):
                    exclude.append(w)
        include = [w for w in include if w not in exclude]
        return {
            "include": include,
            "exclude": exclude,
            "temporal_modifiers": _temporal_modifiers(),
            "compact_words": (data.get("compact_words") if isinstance(data, dict)
                               and isinstance(data.get("compact_words"), list)
                               else _COMPACT_WORDS_BUILTIN),
        }
    except Exception:
        return {
            "include": list(_TRIGGERS_BUILTIN_INCLUDE),
            "exclude": [],
            "temporal_modifiers": _TEMPORAL_MODIFIERS_BUILTIN,
            "compact_words": _COMPACT_WORDS_BUILTIN,
        }


# ============================================================
# Compaction-boundary lookup: an optional feature for callers whose
# ingest format ("claude_code") marks context-compaction boundaries as
# type=meta rows starting with one of ingest.META_HEADS. Scans
# data_dir/main/*.jsonl directly rather than going through the FTS
# index, since this is a rare, cheap lookup.
# ============================================================
try:
    from .ingest import META_HEADS
except Exception:
    META_HEADS = ("This session is being continued",)


def _main_dir():
    return os.path.join(data_dir(), "main")


def _iter_session_lines(session):
    out = []
    try:
        for fp in sorted(glob.glob(os.path.join(_main_dir(), "*.jsonl"))):
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line or session not in line:
                            continue
                        try:
                            d = json.loads(line)
                        except Exception:
                            continue
                        if d.get("session") != session:
                            continue
                        out.append((d.get("ts") or "", d.get("type") or "text", d.get("text") or ""))
            except Exception:
                continue
    except Exception:
        pass
    return out


def compact_marks(session):
    """Timestamps of this session's compaction boundaries, newest first."""
    try:
        lines = _iter_session_lines(session)
        marks = [ts for ts, typ, text in lines
                 if typ == "meta" and any(text.startswith(h) for h in META_HEADS)]
        return sorted(set(marks), reverse=True)
    except Exception:
        return []


def compact_range(session, n):
    """The (start, end) time range of the n-th compaction boundary back
    (n=1 is the most recent). Returns (None, mark_count) if n is out of
    range."""
    try:
        marks = compact_marks(session)
        if n < 1 or n > len(marks):
            return None, len(marks)
        end = marks[n - 1]
        if n < len(marks):
            start = marks[n]
        else:
            lines = _iter_session_lines(session)
            all_ts = sorted(ts for ts, typ, text in lines if ts)
            start = all_ts[0] if all_ts else None
        return start, end
    except Exception:
        return None, 0
