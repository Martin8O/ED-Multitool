"""The remembered exploration: every body you mapped and every species you analysed, in
`EDMultitool/data/explored.db` (SQLite, the copy bundled with Python — EDMC ships it too).

Used on the main thread only. Events are applied in journal order and the rules compare journal times,
so reading the same journal file again (the newest one is read again on every start) changes nothing:
- mapped / analysed: remembered once (the first time counts);
- a sale marks the remembered data it covers as sold: cartographic data by system NAME (the journal names
  no ids there), exobiology one sample per entry, oldest first. Applying a sale again marks only what it
  has not marked yet (an exobiology sample remembers which sale sold it), so a sale seen before the data
  it covers is in the store (live, while the history pass runs) still applies when that data arrives;
- a death forgets everything not sold before it — that data is lost and the bodies can be done again.
"""

import hashlib
import logging
import sqlite3
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from edmultitool.core.events import (
    BodyMapped,
    Died,
    ExplorationSold,
    ExploreEvent,
    OrganicSold,
    SpeciesAnalysed,
)
from edmultitool.core.explored import Explored, Located, body_id64, species_key

SCHEMA_VERSION = 2  # an older file is put aside and rebuilt from the journal history

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mapped (
    system INTEGER NOT NULL, body INTEGER NOT NULL, system_name TEXT NOT NULL, at TEXT NOT NULL,
    sold INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (system, body));
CREATE TABLE IF NOT EXISTS analysed (
    system INTEGER NOT NULL, body INTEGER NOT NULL, species TEXT NOT NULL, at TEXT NOT NULL,
    sold INTEGER NOT NULL DEFAULT 0, sale TEXT NOT NULL DEFAULT '', PRIMARY KEY (system, body, species));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class ExploredStore:
    def __init__(self, conn: sqlite3.Connection, log: logging.Logger) -> None:
        self._conn = conn
        self._log = log
        self._snapshot: Explored | None = None

    @classmethod
    def open(cls, path: Path, log: logging.Logger) -> "ExploredStore":
        """The store at `path`; an unreadable file is kept aside and a fresh one started (the journal
        history rebuilds it), and when even that fails the store lives in memory for this session."""
        try:
            return cls(_connect(path), log)
        except sqlite3.DatabaseError as exc:
            log.warning("could not open %s (%s); rebuilding it from the journal history", path, exc)
        try:
            path.replace(path.with_suffix(".bad"))
            return cls(_connect(path), log)
        except (OSError, sqlite3.DatabaseError) as exc:
            log.warning("could not start %s (%s); remembering this session only", path, exc)
        return cls(_connect(None), log)

    def close(self) -> None:
        self._conn.close()

    @property
    def scanned_mtime(self) -> float:
        """Newest journal file time the history pass has read (0 = never read)."""
        row = self._conn.execute("SELECT value FROM meta WHERE key = 'scanned_mtime'").fetchone()
        try:
            return float(row[0]) if row else 0.0
        except ValueError:
            return 0.0

    def apply(self, events: Iterable[Located], scanned_mtime: float | None = None) -> bool:
        """Apply events in journal order (one transaction); True when what is done changed."""
        changed = False
        with self._conn:
            for event, system_name in events:
                changed |= self._apply(event, system_name)
            if scanned_mtime is not None:
                self._conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('scanned_mtime', ?)",
                    (repr(scanned_mtime),),
                )
        if changed:
            self._snapshot = None
        return changed

    def snapshot(self) -> Explored:
        """Everything done, for route building (an immutable copy, safe to hand to the worker)."""
        if self._snapshot is None:
            mapped = frozenset(
                body_id64(s, b) for s, b in self._conn.execute("SELECT system, body FROM mapped")
            )
            analysed = frozenset(
                (body_id64(s, b), sp)
                for s, b, sp in self._conn.execute("SELECT system, body, species FROM analysed")
            )
            self._snapshot = Explored(mapped, analysed)
        return self._snapshot

    def counts(self) -> tuple[int, int]:
        """(bodies mapped, species analysed) — for the debug dump and the log."""
        snap = self.snapshot()
        return len(snap.mapped), len(snap.analysed)

    def _apply(self, event: ExploreEvent, system_name: str) -> bool:
        c = self._conn
        match event:
            case BodyMapped():
                cur = c.execute(
                    "INSERT OR IGNORE INTO mapped (system, body, system_name, at) VALUES (?, ?, ?, ?)",
                    (event.system_address, event.body_id, system_name, event.when),
                )
                return cur.rowcount > 0
            case SpeciesAnalysed():
                cur = c.execute(
                    "INSERT OR IGNORE INTO analysed (system, body, species, at) VALUES (?, ?, ?, ?)",
                    (event.system_address, event.body_id, species_key(event.species), event.when),
                )
                return cur.rowcount > 0
            case ExplorationSold():  # marking sold again is harmless: no record of the sale needed
                c.executemany(
                    "UPDATE mapped SET sold = 1 WHERE sold = 0 AND at <= ? AND system_name = ?",
                    [(event.when, name) for name in event.systems],
                )
                return False  # selling does not change what is done
            case OrganicSold():
                sale = _sale_key(event)
                for species, count in Counter(species_key(n) for n in event.species).items():
                    marked = c.execute(
                        "SELECT COUNT(*) FROM analysed WHERE sale = ? AND species = ?", (sale, species)
                    ).fetchone()[0]
                    c.execute(
                        "UPDATE analysed SET sold = 1, sale = ? WHERE rowid IN (SELECT rowid FROM analysed"
                        " WHERE sold = 0 AND at <= ? AND species = ? ORDER BY at, rowid LIMIT ?)",
                        (sale, event.when, species, max(0, count - marked)),
                    )
                return False
            case Died():
                lost = c.execute("DELETE FROM mapped WHERE sold = 0 AND at <= ?", (event.when,)).rowcount
                lost += c.execute("DELETE FROM analysed WHERE sold = 0 AND at <= ?", (event.when,)).rowcount
                if lost:
                    self._log.info(
                        "death at %s: %d unsold explored item(s) can be done again", event.when, lost
                    )
                return lost > 0
        return False


def _sale_key(event: OrganicSold) -> str:
    """One exobiology sale, the same every time its journal line is read."""
    return hashlib.sha1("\n".join((event.when, *event.species)).encode("utf-8")).hexdigest()


def _connect(path: Path | None) -> sqlite3.Connection:
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(":memory:" if path is None else str(path))
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, SCHEMA_VERSION):
            raise sqlite3.DatabaseError(f"unknown schema version {version}")
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    except sqlite3.DatabaseError:
        conn.close()
        raise
    return conn
