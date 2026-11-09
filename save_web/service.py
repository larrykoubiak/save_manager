"""Persistent device inventory, serialized background scans, and reviewed transfers."""

from datetime import datetime, timezone
import logging
from pathlib import Path
import threading
import time
import uuid

from save_core.save_copy import apply_copy, plan_copy, plan_slot_copy, plan_delete
from save_core.save_merge import digest, stable_read
from .database import Database
from .inventory import card_path, snapshot_location


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def text(value, label, limit=200):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'{label} must contain 1–{limit} characters.')
    return value.strip()


def poll_interval(value):
    if isinstance(value, bool) or not isinstance(value, int) or (value != 0 and not 60 <= value <= 604800):
        raise ValueError('Polling must be 0 (manual) or 60–604800 seconds.')
    return value


class Service:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.db = Database(self.data_dir / 'inventory.sqlite3')
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.previews = {}
        from .sync import Sync
        self.sync = Sync(self)
        with self.db.connect() as db:
            db.execute("UPDATE jobs SET status='interrupted', finished=?, error='Server restarted; scan again.' WHERE status IN ('queued','running')", (now(),))
            db.execute("UPDATE transfers SET status='expired', finished=?, error='Server restarted; preview again.' WHERE status='preview'", (now(),))
            db.execute("UPDATE transfers SET status='interrupted', finished=?, error='Server stopped during transfer; inspect card and backup before retrying.' WHERE status='applying'", (now(),))

    def create_device(self, body):
        name = text(body.get('name'), 'Device name')
        kind = text(body.get('kind', 'Other'), 'Device type', 80)
        interval = poll_interval(body.get('poll_seconds', 0))
        with self.lock, self.db.connect() as db:
            key = db.execute('INSERT INTO devices(name,kind,poll_seconds) VALUES (?,?,?)',
                             (name, kind, interval)).lastrowid
        return self.db.one('SELECT * FROM devices WHERE id=?', (key,))

    def update_device(self, key, body):
        with self.lock:
            old = self.db.one('SELECT * FROM devices WHERE id=?', (key,))
            name = text(body.get('name', old['name']), 'Device name')
            kind = text(body.get('kind', old['kind']), 'Device type', 80)
            interval = poll_interval(body.get('poll_seconds', old['poll_seconds']))
            with self.db.connect() as db:
                db.execute('UPDATE devices SET name=?,kind=?,poll_seconds=?,next_poll=? WHERE id=?',
                           (name, kind, interval, time.time() + interval, key))
        return self.db.one('SELECT * FROM devices WHERE id=?', (key,))

    def save_location(self, device_id, body, key=None):
        with self.lock:
            self.db.one('SELECT id FROM devices WHERE id=?', (device_id,))
            old = self.db.one('SELECT * FROM locations WHERE id=? AND device_id=?', (key, device_id)) if key else {}
            values = {**old, **body}
            name = text(values.get('name'), 'Location name')
            path = Path(text(values.get('path'), 'Absolute location path', 4096)).expanduser()
            if not path.is_absolute():
                raise ValueError('Use an absolute path as seen by the server/container.')
            path = path.resolve()
            if path == self.data_dir or path in self.data_dir.parents or self.data_dir in path.parents:
                raise ValueError('Card locations and application data must be separate folders.')
            suffix = values.get('suffix', '.ps2')
            if suffix not in ('.ps2', '.bin'):
                raise ValueError('This version reads PS2 .ps2 and .bin card images.')
            marker = values.get('marker', '')
            if not isinstance(marker, str) or len(marker) > 200 or (marker and (Path(marker).name != marker or marker in ('.', '..'))):
                raise ValueError('Availability marker must be a filename inside the location.')
            writable = values.get('writable', False)
            if not isinstance(writable, (bool, int)) or writable not in (0, 1):
                raise ValueError('Writable must be true or false.')
            for other in self.db.rows('SELECT id,path FROM locations'):
                other_path = Path(other['path'])
                if other['id'] != key and (path == other_path or path in other_path.parents or other_path in path.parents):
                    raise ValueError('Locations must not overlap; register each folder once.')
            if key and (str(path) != old['path'] or suffix != old['suffix']):
                raise ValueError('Add a new location to change path or format; existing history belongs to this location.')
            args = (name, str(path), suffix, int(writable), marker)
            with self.db.connect() as db:
                if key:
                    db.execute('UPDATE locations SET name=?,path=?,suffix=?,writable=?,marker=? WHERE id=?', args + (key,))
                else:
                    key = db.execute('INSERT INTO locations(name,path,suffix,writable,marker,device_id) VALUES (?,?,?,?,?,?)',
                                     args + (device_id,)).lastrowid
        return self.db.one('SELECT * FROM locations WHERE id=?', (key,))

    def inventory(self):
        # One SQLite read transaction gives the UI a coherent inventory.
        with self.db.connect() as db:
            db.execute('BEGIN')
            result = {name: [dict(row) for row in db.execute(query)] for name, query in {
                'devices': 'SELECT * FROM devices ORDER BY id',
                'locations': 'SELECT * FROM locations ORDER BY id',
                'cards': 'SELECT * FROM cards ORDER BY filename',
                'saves': '''SELECT s.*, (SELECT count(*) FROM save_versions v WHERE v.save_id=s.id) AS version_count
                            FROM saves s ORDER BY title,directory''',
                'jobs': 'SELECT * FROM jobs ORDER BY id DESC LIMIT 50',
                'slots': '''SELECT p.* FROM save_slots p JOIN saves s ON s.id=p.save_id
                            JOIN cards c ON c.id=s.card_id WHERE s.present=1 AND c.present=1
                            ORDER BY p.save_id,p.number''',
                'transfers': 'SELECT * FROM transfers ORDER BY created DESC LIMIT 50',
                'sync_groups': 'SELECT * FROM sync_groups ORDER BY directory',
                'sync_members': 'SELECT * FROM sync_members ORDER BY group_id,card_id',
            }.items()}
        return result

    def queue_scan(self, device_id, reason='manual'):
        with self.lock, self.db.connect() as db:
            if not db.execute('SELECT id FROM devices WHERE id=?', (device_id,)).fetchone():
                raise LookupError('Device not found.')
            if not db.execute('SELECT id FROM locations WHERE device_id=?', (device_id,)).fetchone():
                raise ValueError('Add a storage location before scanning.')
            existing = db.execute("SELECT * FROM jobs WHERE device_id=? AND status IN ('queued','running')", (device_id,)).fetchone()
            if existing:
                return dict(existing)
            key = db.execute("INSERT INTO jobs(device_id,status,reason,created) VALUES (?,'queued',?,?)",
                             (device_id, reason, now())).lastrowid
            interval = db.execute('SELECT poll_seconds FROM devices WHERE id=?', (device_id,)).fetchone()[0]
            db.execute('UPDATE devices SET next_poll=? WHERE id=?', (time.time() + interval, device_id))
        self.wake.set()
        return self.db.one('SELECT * FROM jobs WHERE id=?', (key,))

    def _store_snapshot(self, location_id, cards, timestamp):
        with self.db.connect() as db:
            db.execute('UPDATE cards SET present=0 WHERE location_id=?', (location_id,))
            db.execute('UPDATE saves SET present=0 WHERE card_id IN (SELECT id FROM cards WHERE location_id=?)', (location_id,))
            for card in cards:
                db.execute('''INSERT INTO cards(location_id,filename,sha256,size,mtime_ns,formatted,total_blocks,free_blocks,last_seen)
                              VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(location_id,filename) DO UPDATE SET
                              sha256=excluded.sha256,size=excluded.size,mtime_ns=excluded.mtime_ns,formatted=excluded.formatted,
                              total_blocks=excluded.total_blocks,free_blocks=excluded.free_blocks,last_seen=excluded.last_seen,present=1''',
                           (location_id, card['filename'], card['sha256'], card['size'], card['mtime_ns'], card['formatted'],
                            card['total_blocks'], card['free_blocks'], timestamp))
                card_id = db.execute('SELECT id FROM cards WHERE location_id=? AND filename=?', (location_id, card['filename'])).fetchone()[0]
                observed = time.time()
                db.execute('''INSERT INTO sync_observations(card_id,sha256,since,checked,checks) VALUES (?,?,?,?,1)
                              ON CONFLICT(card_id) DO UPDATE SET
                              since=CASE WHEN sha256=excluded.sha256 THEN since ELSE excluded.since END,
                              checks=CASE WHEN sha256=excluded.sha256 THEN checks+1 ELSE 1 END,
                              sha256=excluded.sha256,checked=excluded.checked''',
                           (card_id,card['sha256'],observed,observed))
                for save in card['saves']:
                    updated = save['updated'].isoformat(sep=' ') if save['updated'] else None
                    model = save.get('icon_model')
                    icon_hash = digest(model) if model else None
                    if model:
                        db.execute('INSERT OR IGNORE INTO icons(sha256,model) VALUES (?,?)', (icon_hash, model))
                    db.execute('''INSERT INTO saves(card_id,directory,title,updated,sha256,last_seen,icon_hash,row_title,summary,size_blocks) VALUES (?,?,?,?,?,?,?,?,?,?)
                                  ON CONFLICT(card_id,directory) DO UPDATE SET title=excluded.title,updated=excluded.updated,
                                  sha256=excluded.sha256,last_seen=excluded.last_seen,icon_hash=excluded.icon_hash,
                                  row_title=excluded.row_title,summary=excluded.summary,size_blocks=excluded.size_blocks,present=1''',
                               (card_id, save['save'], save['title'], updated, save['sha256'], timestamp, icon_hash,
                                save.get('row_title', ''), save.get('summary', ''), save.get('size_blocks')))
                    save_id = db.execute('SELECT id FROM saves WHERE card_id=? AND directory=?', (card_id, save['save'])).fetchone()[0]
                    db.execute('DELETE FROM save_slots WHERE save_id=?', (save_id,))
                    for slot in save.get('slots', []):
                        db.execute('INSERT INTO save_slots(save_id,number,state,label,sha256,error) VALUES (?,?,?,?,?,?)',
                                   (save_id, slot['number'], slot['state'], slot['label'], slot['sha256'], slot['error']))
                    db.execute('''INSERT INTO save_versions(save_id,sha256,first_seen,last_seen) VALUES (?,?,?,?)
                                  ON CONFLICT(save_id,sha256) DO UPDATE SET last_seen=excluded.last_seen''',
                               (save_id, save['sha256'], timestamp, timestamp))
            db.execute("UPDATE locations SET status='online',error=NULL,last_scanned=?,last_seen=? WHERE id=?",
                       (timestamp, timestamp, location_id))

    def run_scan(self, job_id):
        with self.lock:
            job = self.db.one('SELECT * FROM jobs WHERE id=?', (job_id,))
            if job['status'] != 'queued':
                return
            with self.db.connect() as db:
                db.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
            errors, states = [], []
            timestamp = now()
            for location in self.db.rows('SELECT * FROM locations WHERE device_id=?', (job['device_id'],)):
                try:
                    cards = snapshot_location(location)
                    self._store_snapshot(location['id'], cards, timestamp)
                    states.append('online')
                except Exception as error:
                    state = 'offline' if isinstance(error, OSError) else 'error'
                    states.append(state)
                    errors.append(location['name'] + ': ' + str(error))
                    with self.db.connect() as db:
                        db.execute('UPDATE locations SET status=?,error=?,last_scanned=? WHERE id=?',
                                   (state, str(error), timestamp, location['id']))
            status = 'online' if states and all(s == 'online' for s in states) else (
                'partial' if 'online' in states else 'error' if 'error' in states else 'offline')
            error_text = '\n'.join(errors) or None
            with self.db.connect() as db:
                db.execute('''UPDATE devices SET status=?,error=?,last_checked=?,last_seen=CASE WHEN ? THEN ? ELSE last_seen END,
                              next_poll=?+poll_seconds WHERE id=?''',
                           (status, error_text, timestamp, 'online' in states, timestamp, time.time(), job['device_id']))
                db.execute('UPDATE jobs SET status=?,finished=?,error=? WHERE id=?',
                           ('failed' if errors else 'done', now(), error_text, job_id))

    def enqueue_due(self, at=None):
        at = time.time() if at is None else at
        for device in self.db.rows('''SELECT * FROM devices d WHERE poll_seconds>0 AND next_poll<=?
                                     AND EXISTS(SELECT 1 FROM locations l WHERE l.device_id=d.id)''', (at,)):
            self.queue_scan(device['id'], 'poll')

    def _worker(self):
        while not self.stop_event.is_set():
            self.wake.clear()
            jobs = []
            try:
                with self.lock:
                    self._expire_previews()
                self.enqueue_due()
                self.sync.enqueue_due()
                jobs = self.db.rows("SELECT id FROM jobs WHERE status='queued' ORDER BY id LIMIT 1")
                if jobs:
                    self.run_scan(jobs[0]['id'])
                    self.sync.tick()
                    continue
                self.sync.tick()
            except Exception:
                logging.exception('Background scan failed')
                if jobs:
                    with self.db.connect() as db:
                        db.execute("UPDATE jobs SET status='failed',finished=?,error='Unexpected scan failure; see server log.' WHERE id=?", (now(), jobs[0]['id']))
            self.wake.wait(1)

    def start(self):
        if not self.thread:
            self.thread = threading.Thread(target=self._worker, name='inventory-scanner', daemon=True)
            self.thread.start()

    def close(self):
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=10)

    def _card(self, key, writable=False):
        card = self.db.one('SELECT * FROM cards WHERE id=? AND present=1', (key,))
        location = self.db.one('SELECT * FROM locations WHERE id=?', (card['location_id'],))
        if location['status'] != 'online':
            raise ValueError('Scan this location successfully before transferring saves.')
        if writable and not location['writable']:
            raise ValueError('Destination location is read-only. Enable writes in its settings first.')
        path = card_path(location, card['filename'])
        if digest(stable_read(path)) != card['sha256']:
            raise ValueError('Card changed since its last scan. Scan the device and preview again.')
        return card, location, path

    def _expire_previews(self):
        expired = [key for key, item in self.previews.items() if item[0] < time.monotonic()]
        for key in expired:
            del self.previews[key]
            with self.db.connect() as db:
                db.execute("UPDATE transfers SET status='expired',finished=? WHERE id=? AND status='preview'", (now(), key))

    def preview(self, body):
        with self.lock:
            action = body.get('action', 'copy')
            if action not in ('copy', 'delete'):
                raise ValueError('Unknown save action.')
            self._expire_previews()
            if len(self.previews) >= 4:
                raise ValueError('Four previews are already open. Cancel one or wait ten minutes.')
            save = self.db.one('SELECT * FROM saves WHERE id=? AND present=1', (body.get('save_id'),))
            source, _, source_path = self._card(save['card_id'], writable=action == 'delete')
            if action == 'delete':
                destination, destination_path = source, source_path
            else:
                destination, _, destination_path = self._card(body.get('destination_card_id'), writable=True)
            record = {'save': save['directory'], 'card_version': (source['size'], source['mtime_ns'])}
            if action == 'delete':
                if 'source_slot' in body or 'destination_slot' in body:
                    raise ValueError('Deletion removes a whole save directory, including all its slots.')
                plan = plan_delete(source_path, record)
            elif 'source_slot' in body or 'destination_slot' in body:
                plan = plan_slot_copy(source_path, record, body.get('source_slot'), destination_path,
                                      body.get('destination_slot'), body.get('conflict', 'stop'))
            else:
                plan = plan_copy(source_path, record, destination_path, body.get('conflict', 'stop'))
            # Tie preview to the exact scanned inventories, including changes during planning.
            if plan.source_hash != source['sha256'] or plan.destination_hash != destination['sha256']:
                raise ValueError('A card changed during preview. Scan and try again.')
            key = uuid.uuid4().hex
            with self.db.connect() as db:
                db.execute('''INSERT INTO transfers(id,source_card_id,destination_card_id,directory,destination_save,
                              source_hash,destination_hash,action,status,created,source_slot,destination_slot)
                              VALUES (?,?,?,?,?,?,?,?,'preview',?,?,?)''',
                           (key, source['id'], destination['id'], save['directory'], plan.destination_save,
                            plan.source_hash, plan.destination_hash, plan.action, now(), plan.source_slot, plan.destination_slot))
            self.previews[key] = (time.monotonic() + 600, plan)
            return {**self.db.one('SELECT * FROM transfers WHERE id=?', (key,)),
                    'warnings': list(plan.warnings), 'expires_in': 600,
                    'source': str(plan.source), 'destination': str(plan.destination)}

    def cancel_preview(self, key):
        with self.lock:
            self.db.one('SELECT * FROM transfers WHERE id=?', (key,))
            self.previews.pop(key, None)
            with self.db.connect() as db:
                db.execute("UPDATE transfers SET status='cancelled',finished=? WHERE id=? AND status='preview'", (now(), key))

    def apply(self, key, body):
        if body.get('cards_idle') is not True:
            raise ValueError('Confirm that games and emulators using both cards are closed.')
        with self.lock:
            self._expire_previews()
            transfer = self.db.one('SELECT * FROM transfers WHERE id=?', (key,))
            if key not in self.previews or transfer['status'] != 'preview':
                raise ValueError('Preview expired or was already used. Create a new preview.')
            _, plan = self.previews.pop(key)
            with self.db.connect() as db:
                db.execute("UPDATE transfers SET status='applying' WHERE id=?", (key,))
            try:
                _, _, source_path = self._card(transfer['source_card_id'])
                _, destination_location, destination_path = self._card(transfer['destination_card_id'], writable=True)
                if source_path != plan.source or destination_path != plan.destination:
                    raise ValueError('A location changed since preview. Scan and preview again.')
                backup = apply_copy(plan, self.data_dir / 'backups')
            except Exception as error:
                with self.db.connect() as db:
                    db.execute("UPDATE transfers SET status='failed',finished=?,error=?,backup=? WHERE id=?", (now(), str(error), getattr(error, 'backup', None), key))
                raise
            with self.db.connect() as db:
                db.execute("UPDATE transfers SET status='done',finished=?,backup=? WHERE id=?", (now(), str(backup) if backup else None, key))
            # The old snapshot remains identifiable until the rescan completes.
            self.queue_scan(destination_location['device_id'], 'transfer')
            return self.db.one('SELECT * FROM transfers WHERE id=?', (key,))
