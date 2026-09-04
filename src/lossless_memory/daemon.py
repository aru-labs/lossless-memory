# -*- coding: utf-8 -*-
"""daemon -- a minimal background process that keeps the index fresh.

Every config's daemon_interval seconds (default 600), calls
auto_index.run() so newly appended source lines get converted and
indexed without anyone having to run it by hand. That's the whole
job: no request/response server, no cross-process protocol -- recall
just reads whatever index is on disk at query time, so there is
nothing for this process to serve.

    python -m lossless_memory.daemon           # run forever
    python -m lossless_memory.daemon --once     # run one pass and exit
"""
import sys
import time
import traceback
from datetime import datetime

from .config import config
from . import auto_index


def _log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def tick():
    """One pass: reindex if the source changed. Returns True if it did."""
    try:
        updated, msg = auto_index.run(force=False, quiet=True)
        _log(msg)
        return updated
    except Exception:
        _log("indexing error:\n" + traceback.format_exc())
        return False


def main():
    once = "--once" in sys.argv
    interval = int(config().get("daemon_interval") or 600)
    _log("daemon starting" + (" (--once)" if once else f" (every {interval}s)"))
    tick()
    if once:
        return
    while True:
        try:
            time.sleep(interval)
            tick()
        except KeyboardInterrupt:
            _log("stop requested, exiting")
            break
        except Exception:
            _log("loop error (continuing):\n" + traceback.format_exc())


if __name__ == "__main__":
    main()
