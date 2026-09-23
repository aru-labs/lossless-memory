# -*- coding: utf-8 -*-
"""Single gateway for configuration.

Every path and every name the rest of this package needs comes through
config() -- no module hardcodes a personal path, a user's name, or an
AI's name. If config.json is missing or broken, generic defaults are
used instead; nothing raises just because the file isn't there.

Set the LM_CONFIG_PATH environment variable to point at a config file
in a location other than the current working directory.
"""
import json
import os
from datetime import datetime

_CONFIG_ENV = "LM_CONFIG_PATH"
_DEFAULT_NAME = "config.json"

# Generic defaults. None of these are project- or person-specific.
_DEFAULTS = {
    # display names used when tagging rows / printing search results
    "user_name": "user",
    "ai_name": "assistant",
    "model_name": "unknown",

    # where converted logs and derived indexes are written (created on demand)
    "data_dir": "./logs",
    # source directory ingest.py reads from (raw per-session JSONL files)
    "raw_log_dir": None,
    # which ingest.py parser to use: "plain" or "claude_code"
    "ingest_format": "plain",

    # the timezone time words ("today", "last night") are read in and days
    # are bucketed by: an IANA name such as "America/New_York" or
    # "Asia/Tokyo"; None = this machine's local time
    "timezone": None,

    # LLL (state_index) tuning
    "wake_word": "good morning",   # greeting that resets the "current topic" window
    "bridge_minutes": 30,
    "daemon_interval": 600,

    # extra vocabulary merged into the built-in defaults, not a replacement
    "topic_words": [],
    "protected_names": [],

    # optional integrations -- absent by default, silently skipped if unset
    "asr_corrections": None,   # path to a JSON {"wrong": "right"} correction dict
    "origin_log": None,        # path to a tab-separated "<iso-ts>\t<tag>" log
}

_cache = None
_cache_path = None


def _config_path():
    return os.environ.get(_CONFIG_ENV) or os.path.join(os.getcwd(), _DEFAULT_NAME)


def config(force_reload=False):
    """Return the effective config dict (defaults merged with config.json)."""
    global _cache, _cache_path
    path = _config_path()
    if not force_reload and _cache is not None and _cache_path == path:
        return _cache
    data = {}
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
    except Exception:
        data = {}
    out = dict(_DEFAULTS)
    if isinstance(data, dict):
        out.update({k: v for k, v in data.items() if not str(k).startswith("_")})
    _cache = out
    _cache_path = path
    return out


def data_dir():
    """Base directory for converted logs and derived indexes. Created if
    it doesn't exist yet."""
    d = config().get("data_dir") or "./logs"
    os.makedirs(d, exist_ok=True)
    return os.path.abspath(d)


_tz_cache = None
_tz_cache_key = None


def user_tz():
    """The timezone the user's time words ("today", "last night") are
    read in, and the one days are bucketed by. config "timezone" is an
    IANA name (e.g. "Asia/Tokyo", "America/New_York"); unset means this
    machine's local time. An unknown name falls back to local time with
    a one-time warning, rather than silently searching the wrong day.
    Note: the local-time fallback is a fixed UTC offset, so in a place
    with daylight saving time, set "timezone" explicitly."""
    global _tz_cache, _tz_cache_key
    name = config().get("timezone")
    if _tz_cache is not None and _tz_cache_key == name:
        return _tz_cache
    tz = None
    if name:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(str(name))
        except Exception as ex:
            import warnings
            warnings.warn("lossless_memory: unknown timezone %r (%s); using this "
                          "machine's local time instead" % (name, type(ex).__name__))
    if tz is None:
        tz = datetime.now().astimezone().tzinfo
    _tz_cache, _tz_cache_key = tz, name
    return tz
