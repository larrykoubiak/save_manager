"""SQLite inventory. Each operation owns its connection; snapshots commit atomically."""

from contextlib import contextmanager
from pathlib import Path
import os
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
    root_path TEXT NOT NULL DEFAULT '/',
    transport TEXT NOT NULL DEFAULT 'filesystem', ftp_host TEXT NOT NULL DEFAULT '',
    ftp_port INTEGER NOT NULL DEFAULT 21, ftp_username TEXT NOT NULL DEFAULT 'anonymous',
    ftp_password TEXT NOT NULL DEFAULT '',
    poll_seconds INTEGER NOT NULL DEFAULT 0, last_checked TEXT, last_seen TEXT,
    status TEXT NOT NULL DEFAULT 'never', error TEXT, next_poll REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY, device_id INTEGER NOT NULL REFERENCES devices(id),
    name TEXT NOT NULL, path TEXT NOT NULL, suffix TEXT NOT NULL,
    writable INTEGER NOT NULL DEFAULT 0, marker TEXT NOT NULL DEFAULT '',
    backend TEXT NOT NULL DEFAULT 'ps2',
    status TEXT NOT NULL DEFAULT 'never', error TEXT, last_scanned TEXT, last_seen TEXT,
    deleted INTEGER NOT NULL DEFAULT 0
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
    id INTEGER PRIMARY KEY, directory TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1, head_hash TEXT, source_card_id INTEGER REFERENCES cards(id),
    snapshot TEXT, status TEXT NOT NULL DEFAULT 'settling', message TEXT NOT NULL DEFAULT '',
    next_scan REAL NOT NULL DEFAULT 0, updated TEXT NOT NULL,
    backend TEXT NOT NULL DEFAULT 'ps2', UNIQUE(backend,directory)
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
        self.path.touch(mode=0o600,exist_ok=True)
        self.path.chmod(0o600)
        with self.connect() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 10:
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
            if 'backend' not in {row[1] for row in db.execute('PRAGMA table_info(locations)')}:
                db.execute("ALTER TABLE locations ADD COLUMN backend TEXT NOT NULL DEFAULT 'ps2'")
            if 'backend' not in {row[1] for row in db.execute('PRAGMA table_info(sync_groups)')}:
                # Keep IDs so memberships and accepted snapshots remain valid.
                db.commit()
                db.execute('PRAGMA foreign_keys=OFF')
                try:
                    with db:
                        db.execute('BEGIN IMMEDIATE')
                        db.execute('''CREATE TABLE sync_groups_new (
                            id INTEGER PRIMARY KEY, directory TEXT NOT NULL,
                            enabled INTEGER NOT NULL DEFAULT 1, head_hash TEXT,
                            source_card_id INTEGER REFERENCES cards(id), snapshot TEXT,
                            status TEXT NOT NULL DEFAULT 'settling', message TEXT NOT NULL DEFAULT '',
                            next_scan REAL NOT NULL DEFAULT 0, updated TEXT NOT NULL,
                            backend TEXT NOT NULL DEFAULT 'ps2', UNIQUE(backend,directory))''')
                        db.execute('''INSERT INTO sync_groups_new
                            (id,directory,enabled,head_hash,source_card_id,snapshot,status,message,next_scan,updated)
                            SELECT id,directory,enabled,head_hash,source_card_id,snapshot,status,message,next_scan,updated
                            FROM sync_groups''')
                        db.execute('DROP TABLE sync_groups')
                        db.execute('ALTER TABLE sync_groups_new RENAME TO sync_groups')
                finally:
                    db.execute('PRAGMA foreign_keys=ON')
                if db.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError('Backend migration failed its foreign-key check.')
            if version < 9:
                db.commit()
                db.execute('PRAGMA foreign_keys=OFF')
                try:
                    with db:
                        db.execute('BEGIN IMMEDIATE')
                        if 'root_path' not in {row[1] for row in db.execute('PRAGMA table_info(devices)')}:
                            db.execute("ALTER TABLE devices ADD COLUMN root_path TEXT NOT NULL DEFAULT '/'")
                        # Keep every location/card ID and history entry while moving
                        # the path uniqueness constraint under its owning device.
                        db.execute('''CREATE TABLE locations_new (
                            id INTEGER PRIMARY KEY, device_id INTEGER NOT NULL REFERENCES devices(id),
                            name TEXT NOT NULL, path TEXT NOT NULL, suffix TEXT NOT NULL,
                            writable INTEGER NOT NULL DEFAULT 0, marker TEXT NOT NULL DEFAULT '',
                            backend TEXT NOT NULL DEFAULT 'ps2', status TEXT NOT NULL DEFAULT 'never',
                            error TEXT, last_scanned TEXT, last_seen TEXT, deleted INTEGER NOT NULL DEFAULT 0)''')
                        db.execute('''INSERT INTO locations_new
                            (id,device_id,name,path,suffix,writable,marker,backend,status,error,last_scanned,last_seen)
                            SELECT id,device_id,name,path,suffix,writable,marker,backend,status,error,last_scanned,last_seen
                            FROM locations''')
                        for device in db.execute('SELECT id FROM devices').fetchall() if version < 8 else ():
                            locations = db.execute('SELECT id,path FROM locations_new WHERE device_id=?', (device['id'],)).fetchall()
                            if locations:
                                root = Path(os.path.commonpath([str(Path(row['path']).parent) for row in locations]))
                                db.execute('UPDATE devices SET root_path=? WHERE id=?', (str(root), device['id']))
                                for row in locations:
                                    db.execute('UPDATE locations_new SET path=? WHERE id=?',
                                               (Path(row['path']).relative_to(root).as_posix(), row['id']))
                        db.execute('DROP TABLE locations')
                        db.execute('ALTER TABLE locations_new RENAME TO locations')
                        db.execute('CREATE UNIQUE INDEX active_location_path ON locations(device_id,path) WHERE deleted=0')
                        db.execute('PRAGMA user_version=9')
                finally:
                    db.execute('PRAGMA foreign_keys=ON')
                if db.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError('Device-root migration failed its foreign-key check.')
            columns = {row[1] for row in db.execute('PRAGMA table_info(devices)')}
            for name, definition in (
                ('transport', "TEXT NOT NULL DEFAULT 'filesystem'"),
                ('ftp_host', "TEXT NOT NULL DEFAULT ''"),
                ('ftp_port', 'INTEGER NOT NULL DEFAULT 21'),
                ('ftp_username', "TEXT NOT NULL DEFAULT 'anonymous'"),
                ('ftp_password', "TEXT NOT NULL DEFAULT ''")):
                if name not in columns:
                    db.execute(f'ALTER TABLE devices ADD COLUMN {name} {definition}')
            db.execute('PRAGMA user_version=10')

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
