"""Multi-agent paper-trading engine: traders, analysts and risk monitors."""

import os as _os

# DESK_DATA_DIR (mina, 25 Nov 2026): the persistent-disk folder. When set it becomes EVENTS_DIR, the folder every
# runtime file already lives in (events-*.jsonl, desk_audit.jsonl, desk_state.sqlite, memory, practice books), so
# one mounted disk keeps them all across deploys and restarts. Unset: everything stays where it was.
if _os.environ.get("DESK_DATA_DIR", "").strip():
    _os.environ["EVENTS_DIR"] = _os.environ["DESK_DATA_DIR"].strip()
