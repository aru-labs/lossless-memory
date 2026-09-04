# -*- coding: utf-8 -*-
"""lossless_memory -- lossless long-term memory for a personal AI.

Never summarize, keep every line, put a timestamp on everything.

Typical use:

    from lossless_memory import ingest, auto_index, recall

    ingest.convert_all(fmt="plain", source="./raw")
    auto_index.run()
    for line in recall.recall("2026-09-02 budget"):
        print(line)

See examples/quickstart.md for a full walkthrough and config.example.json
for the settings every module reads through.
"""

__version__ = "0.1.0"
