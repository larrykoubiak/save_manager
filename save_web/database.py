"""SQLite inventory. Each operation owns its connection; snapshots commit atomically."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
    poll_seconds INTEGER NOT NULL DEFAULT 0, last_checked TEXT, last_seen TEXT,
    status TEXT NOT NULL DEFAULT 'never', error TEXT, next_poll REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY, device_id INTEGER NOT NULL REFERENCES devices(id),
    name TEXT NOT NULL, path TEXT NOT NULL UNIQUE, suffix TEXT NOT NULL,
    writable INTEGER NOT NULL DEFAULT 0, marker TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'never', error TEXT, last_scanned TEXT, last_seen TEXT
);
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY, location_id INTEGER NOT NULL REFERENCES locations(id),
    filename TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL, formatted INTEGER NOT NULL,
    total_blocks INTEGER, free_blocks INTEGER, present INTEGER NOT NULL DEFAULT 1,
    last_seen TEXT NOT NULL, UNIQUE(location_id, filename)
);
CREATE TABLE IF NOT EXISTS saves (
    id INTEGER PRIMARY KEY, card_id INTEGER NOT NULL REFERENCES cards(id),
    directory TEXT NOT NULL, title TEXT NOT NULL, updated TEXT, sha256 TEXT NOT NULL,
    icon_hash TEXT,
    present INTEGER NOT NULL DEFAULT 1, last_seen TEXT NOT NULL,
    UNIQUE(card_id, directory)
);
CREATE TABLE IF NOT EXISTS icons (
    sha256 TEXT PRIMARY KEY, model BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS save_slots (
    save_id INTEGER NOT NULL REFERENCES saves(id), number INTEGER NOT NULL,
    state TEXT NOT NULL, label TEXT NOT NULL, sha256 TEXT, error TEXT NOT NULL,
    PRIMARY KEY(save_id, number)
);
CREATE TABLE IF NOT EXISTS save_versions (
    id INTEGER PRIMARY KEY, save_id INTEGER NOT NULL REFERENCES saves(id),
    sha256 TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
    UNIQUE(save_id, sha256)
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY, device_id INTEGER NOT NULL REFERENCES devices(id),
    status TEXT NOT NULL, reason TEXT NOT NULL, created TEXT NOT NULL,
    finished TEXT, error TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_scan_per_device ON jobs(device_id)
    WHERE status IN ('queued', 'running');
CREATE TABLE IF NOT EXISTS transfers (
    id TEXT PRIMARY KEY, source_card_id INTEGER NOT NULL REFERENCES cards(id),
    destination_card_id INTEGER NOT NULL REFERENCES cards(id),
    directory TEXT NOT NULL, destination_save TEXT NOT NULL,
    source_hash TEXT NOT NULL, destination_hash TEXT NOT NULL,
    action TEXT NOT NULL, status TEXT NOT NULL, created TEXT NOT NULL,
    finished TEXT, backup TEXT, error TEXT, source_slot INTEGER, destination_slot INTEGER
);
CREATE TABLE IF NOT EXISTS sync_groups (
    id INTEGER PRIMARY KEY, directory TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1, head_hash TEXT, source_card_id INTEGER REFERENCES cards(id),
    snapshot TEXT, status TEXT NOT NULL DEFAULT 'settling', message TEXT NOT NULL DEFAULT '',
    next_scan REAL NOT NULL DEFAULT 0, updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_members (
    group_id INTEGER NOT NULL REFERENCES sync_groups(id), card_id INTEGER NOT NULL REFERENCES cards(id),
    baseline_hash TEXT, status TEXT NOT NULL DEFAULT 'waiting', message TEXT NOT NULL DEFAULT '',
    retry_after REAL NOT NULL DEFAULT 0,
    PRIMARY KEY(group_id,card_id)
);
CREATE TABLE IF NOT EXISTS sync_observations (
    card_id INTEGER PRIMARY KEY REFERENCES cards(id), sha256 TEXT NOT NULL,
    since REAL NOT NULL, checked REAL NOT NULL, checks INTEGER NOT NULL
);
"""


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 6:
                raise ValueError('This database requires a newer Save Manager.')
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript(SCHEMA)
            if 'icon_hash' not in {row[1] for row in db.execute('PRAGMA table_info(saves)')}:
                db.execute('ALTER TABLE saves ADD COLUMN icon_hash TEXT')
            columns = {row[1] for row in db.execute('PRAGMA table_info(transfers)')}
            for name in ('source_slot', 'destination_slot'):
                if name not in columns:
                    db.execute(f'ALTER TABLE transfers ADD COLUMN {name} INTEGER')
            save_columns = {row[1] for row in db.execute('PRAGMA table_info(saves)')}
            for name in ('row_title', 'summary'):
                if name not in save_columns:
                    db.execute(f"ALTER TABLE saves ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
            if 'size_blocks' not in save_columns:
                db.execute('ALTER TABLE saves ADD COLUMN size_blocks INTEGER')
            db.execute('PRAGMA user_version=6')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def rows(self, sql, args=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, args)]

    def one(self, sql, args=()):
        rows = self.rows(sql, args)
        if not rows:
            raise LookupError('Record not found.')
        return rows[0]
