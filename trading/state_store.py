"""Restart-safe state: a small SQLite file (WAL mode) that keeps what the desk has learned.

mina's state_store.py (24 Nov 2026): save the agents' learning, the dual brains' probation ledgers, Orbit's arm
heat and the Night Shift capsule to SQLite in WAL mode every 15 minutes, so a restart or a crash halfway
through a save never wipes what the desk learned.

trading/memory.py already gathers all of that into one snapshot (every SAVE_EVERY ticks and every 15 minutes).
It used to go only to `memory-<shard>.json`. Now each save also goes into `desk_state.sqlite` under the
shard's name, in one transaction:

- **WAL mode** (`journal_mode=WAL`, `synchronous=NORMAL`): a save is all-or-nothing, and the app can read the
  last good snapshot while a new one is being written.
- **Fallback on start**: if the JSON file is missing or damaged (a crash mid-write, a lost disk), memory.restore
  reads the same snapshot back from SQLite.
- **History**: the last `KEEP` snapshots per shard are kept, so a bad hour of learning can be rolled back.

Env STATE_STORE=off turns the SQLite copy off (JSON only, as before). Paper only: nothing here touches money.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

ENABLED = True  # STATE_STORE=off: JSON memory files only
KEEP = 8  # snapshots kept per namespace


class DeskStateStore:
    """Namespaced JSON snapshots in one SQLite file, written in WAL mode."""

    def __init__(self, db_path: str | Path = "data/desk_state.sqlite") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS snapshots (namespace TEXT NOT NULL, saved REAL NOT NULL,"
                         " payload TEXT NOT NULL)")
            conn.execute("CREATE INDEX IF NOT EXISTS snap_ns ON snapshots (namespace, saved)")

    @contextmanager
    def _connect(self):
        """One transaction on a connection that is always closed afterwards (Windows locks an open file)."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            with conn:  # commit on success, roll back on error
                yield conn
        finally:
            conn.close()

    def journal_mode(self) -> str:
        with self._connect() as conn:
            return conn.execute("PRAGMA journal_mode").fetchone()[0]

    def save_namespace(self, namespace: str, state: dict, now: float | None = None) -> None:
        blob = json.dumps(state, separators=(",", ":"), default=str)
        saved = time.time() if now is None else now
        with self._connect() as conn:  # one transaction: the insert and the trim land together or not at all
            conn.execute("INSERT INTO snapshots (namespace, saved, payload) VALUES (?, ?, ?)", (namespace, saved, blob))
            conn.execute("DELETE FROM snapshots WHERE namespace = ? AND rowid NOT IN (SELECT rowid FROM snapshots"
                         " WHERE namespace = ? ORDER BY saved DESC, rowid DESC LIMIT ?)", (namespace, namespace, KEEP))

    def load_namespace(self, namespace: str, back: int = 0) -> dict | None:
        """The latest snapshot (back=0), or an older one (back=1 is the one before)."""
        with self._connect() as conn:
            row = conn.execute("SELECT payload FROM snapshots WHERE namespace = ? ORDER BY saved DESC, rowid DESC"
                               " LIMIT 1 OFFSET ?", (namespace, back)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except ValueError:
            return None

    def history(self, namespace: str) -> list[float]:
        with self._connect() as conn:
            return [r[0] for r in conn.execute("SELECT saved FROM snapshots WHERE namespace = ? ORDER BY saved DESC",
                                               (namespace,))]

    def status(self) -> dict:
        with self._connect() as conn:
            rows = conn.execute("SELECT namespace, COUNT(*), MAX(saved) FROM snapshots GROUP BY namespace").fetchall()
        return {"file": str(self.db_path), "mode": self.journal_mode(),
                "namespaces": {ns: {"snapshots": n, "last_saved": last} for ns, n, last in rows}}


def for_memory(path: Path) -> tuple[DeskStateStore, str]:
    """The store and namespace that sit next to a memory file: memory-3.json -> desk_state.sqlite, 'memory-3'."""
    path = Path(path)
    return DeskStateStore(path.with_name("desk_state.sqlite")), path.stem
