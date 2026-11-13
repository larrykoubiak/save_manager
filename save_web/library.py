"""Persistent game/slot library, content-addressed storage and bounded history."""

import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import zipfile

from game_plugins.library import extract_slots
from game_plugins import plugin_for
from save_core.backends.serialization import bundle_manifest, read_bundle
from save_core.save_games import game_serial, fallback_title


SCHEMA = '''
CREATE TABLE IF NOT EXISTS library_settings (
    id INTEGER PRIMARY KEY CHECK(id=1), previous_versions INTEGER NOT NULL DEFAULT 2
);
INSERT OR IGNORE INTO library_settings(id) VALUES (1);
CREATE TABLE IF NOT EXISTS library_games (
    id INTEGER PRIMARY KEY, backend TEXT NOT NULL, game_key TEXT NOT NULL, title TEXT NOT NULL, icon_hash TEXT,
    card_ports TEXT NOT NULL DEFAULT 'unknown', card_port_note TEXT NOT NULL DEFAULT '',
    UNIQUE(backend,game_key)
);
CREATE TABLE IF NOT EXISTS library_slots (
    id INTEGER PRIMARY KEY, game_id INTEGER NOT NULL REFERENCES library_games(id),
    slot_key TEXT NOT NULL, number INTEGER, current_version_id INTEGER REFERENCES library_versions(id),
    UNIQUE(game_id,slot_key)
);
CREATE TABLE IF NOT EXISTS library_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, slot_id INTEGER NOT NULL REFERENCES library_slots(id),
    content_hash TEXT NOT NULL, bundle_hash TEXT NOT NULL, label TEXT NOT NULL,
    playtime_seconds INTEGER, source_save_id INTEGER NOT NULL REFERENCES saves(id), created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS library_version_slot ON library_versions(slot_id,id);
CREATE TABLE IF NOT EXISTS library_indexed_versions (
    version_id INTEGER PRIMARY KEY REFERENCES library_versions(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS library_sources (
    save_id INTEGER NOT NULL REFERENCES saves(id), slot_id INTEGER NOT NULL REFERENCES library_slots(id),
    content_hash TEXT NOT NULL, last_seen TEXT NOT NULL, present INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(save_id,slot_id)
);
CREATE TABLE IF NOT EXISTS library_errors (
    save_id INTEGER PRIMARY KEY REFERENCES saves(id), error TEXT NOT NULL,
    bundle_hash TEXT, last_seen TEXT NOT NULL
);
'''


def common_game_title(titles):
    titles = [fallback_title(dict(title=title,save=title)) for title in titles if title]
    if not titles:
        return 'Unknown game'
    prefix = os.path.commonprefix(titles)
    prefix = re.sub(r'(?i)(?:[:/\s]+(?:No\.?|DATA|SAVE|FILE)\s*\d*)$', '',prefix).rstrip(' :/−-_')
    return prefix if len(prefix)>=3 else titles[0]


def regroup_games(db):
    """Merge catalogue headings by platform/serial without changing saved bytes."""
    if not db.in_transaction:
        db.execute('BEGIN IMMEDIATE')
    groups = {}
    for row in db.execute('SELECT * FROM library_games ORDER BY id').fetchall():
        game = dict(row)
        groups.setdefault((game['backend'],game_serial(game['game_key']) or game['game_key']),[]).append(game)
    for (backend,key), games in groups.items():
        keeper = next((game for game in games if game['game_key']==key),games[0])
        titles, plugin_titles = [], []
        notes = list(dict.fromkeys(game['card_port_note'] for game in games if game['card_port_note']))
        ports = {game['card_ports'] for game in games if game['card_ports']!='unknown'}
        selected_ports = next(iter(ports)) if len(ports)==1 else 'unknown'
        if len(ports)>1:
            notes.append('Merged port settings differed: '+', '.join(sorted(ports))+'. Please verify support.')
        for game in games:
            titles.append(game['title'])
            for slot in db.execute('SELECT * FROM library_slots WHERE game_id=? ORDER BY id',(game['id'],)).fetchall():
                origin = db.execute('''SELECT s.directory FROM library_versions v JOIN saves s ON s.id=v.source_save_id
                    WHERE v.slot_id=? ORDER BY v.id LIMIT 1''',(slot['id'],)).fetchone()
                directory = origin[0] if origin else game['game_key'].rsplit(':',1)[-1]
                plugin = plugin_for(directory,backend)
                if plugin:
                    plugin_titles.append(plugin.name)
                lineage = slot['slot_key'] if slot['slot_key'].startswith(directory+':') else directory+':'+slot['slot_key']
                # Preserve every legacy record even if two old adapters used the same lineage key.
                conflict = db.execute('SELECT id FROM library_slots WHERE game_id=? AND slot_key=? AND id!=?',
                                      (keeper['id'],lineage,slot['id'])).fetchone()
                if conflict:
                    lineage += ':legacy-'+str(slot['id'])
                db.execute('UPDATE library_slots SET game_id=?,slot_key=? WHERE id=?',(keeper['id'],lineage,slot['id']))
            if game['id']!=keeper['id']:
                db.execute('DELETE FROM library_games WHERE id=?',(game['id'],))
        db.execute('UPDATE library_games SET game_key=?,title=?,icon_hash=?,card_ports=?,card_port_note=? WHERE id=?',
                   (key,common_game_title(plugin_titles or titles),next((g['icon_hash'] for g in games if g['icon_hash']),None),
                    selected_ports,'\n'.join(notes),keeper['id']))


def json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')


class ObjectStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, key):
        if not re.fullmatch('[0-9a-f]{64}', key):
            raise ValueError('Invalid library object hash.')
        return self.root/key

    def get(self, key):
        data = self.path(key).read_bytes()
        if hashlib.sha256(data).hexdigest()!=key:
            raise ValueError('Library object failed its integrity check.')
        return data

    def put(self, data):
        key = hashlib.sha256(data).hexdigest()
        path = self.path(key)
        if path.exists():
            self.get(key)
            return key
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if hashlib.sha256(temporary.read_bytes()).hexdigest()!=key:
                raise ValueError('Library object write failed verification.')
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return key

    def manifest(self, value):
        return self.put(json_bytes(value))

    def load(self, key):
        return json.loads(self.get(key))


class SaveLibrary:
    def __init__(self, service):
        self.service = service
        self.db = service.db
        self.objects = ObjectStore(service.data_dir/'library'/'objects')

    def index_plugins(self):
        """Expand retained whole saves when an internal-slot plugin becomes available.

        Sources may be offline or removed. Original versions, bundles and heads
        remain intact; derived slot versions refer back to those same bundles.
        """
        with self.service.lock, self.db.connect() as db:
            rows = db.execute('''SELECT v.*,g.id game_id,g.title,s.slot_key,p.directory
                FROM library_versions v JOIN library_slots s ON s.id=v.slot_id
                JOIN library_games g ON g.id=s.game_id JOIN saves p ON p.id=v.source_save_id
                WHERE g.backend='ps2' AND s.number IS NULL AND s.slot_key LIKE '%:whole'
                AND NOT EXISTS(SELECT 1 FROM library_indexed_versions i WHERE i.version_id=v.id)
                ORDER BY v.id''').fetchall()
            derived = set()
            for row in rows:
                plugin = plugin_for(row['directory'], 'ps2')
                if not (callable(getattr(plugin, 'copy_slot', None)) and callable(getattr(plugin, 'library_slots', None))):
                    continue
                try:
                    bundle = read_bundle(self.objects.load(row['bundle_hash']), self.objects.get)
                    _, _, slots = extract_slots('ps2', bundle, row['title'])
                except (ValueError, KeyError, OSError) as error:
                    logging.getLogger(__name__).warning('Cannot index retained save version %s: %s', row['id'], error)
                    continue
                for slot in slots:
                    content_hash = self.objects.manifest({'files':[
                        dict(name=name, object=self.objects.put(data)) for name,data in sorted(slot.files.items())]})
                    db.execute('INSERT OR IGNORE INTO library_slots(game_id,slot_key,number) VALUES (?,?,?)',
                               (row['game_id'],slot.key,slot.number))
                    key = db.execute('SELECT id FROM library_slots WHERE game_id=? AND slot_key=?',
                                     (row['game_id'],slot.key)).fetchone()[0]
                    derived.add(key)
                    if not db.execute('SELECT id FROM library_versions WHERE slot_id=? AND content_hash=?', (key,content_hash)).fetchone():
                        db.execute('''INSERT INTO library_versions
                            (slot_id,content_hash,bundle_hash,label,playtime_seconds,source_save_id,created)
                            VALUES (?,?,?,?,?,?,?)''',
                            (key,content_hash,row['bundle_hash'],slot.label,slot.playtime_seconds,row['source_save_id'],row['created']))
                if slots:
                    # Do not resurrect derived versions after normal history pruning.
                    db.execute('INSERT INTO library_indexed_versions(version_id) VALUES (?)', (row['id'],))
            for key in derived:
                db.execute('''UPDATE library_slots SET current_version_id=(SELECT id FROM library_versions
                    WHERE slot_id=? ORDER BY playtime_seconds IS NOT NULL DESC,playtime_seconds DESC,id DESC LIMIT 1)
                    WHERE id=? AND current_version_id IS NULL''', (key,key))

    def capture(self, db, backend, record, save_id, timestamp):
        db.execute('UPDATE library_sources SET present=0 WHERE save_id=?', (save_id,))
        bundle = record.get('bundle')
        if bundle is None:
            error = record.get('library_error', 'This save layout cannot be extracted yet.')
            db.execute('''INSERT INTO library_errors(save_id,error,last_seen) VALUES (?,?,?)
                          ON CONFLICT(save_id) DO UPDATE SET error=excluded.error,last_seen=excluded.last_seen''',
                       (save_id,error,timestamp))
            return
        # Preserve full backend metadata and shared game files alongside each slot.
        bundle_hash = self.objects.manifest(bundle_manifest(bundle,self.objects.put))
        try:
            game_key, title, slots = extract_slots(backend, bundle, record['title'])
            problem = record.get('slot_error') or '; '.join(
                f"Slot {slot['number']}: {slot['error']}" for slot in record.get('slots',[]) if slot['state']=='invalid')
        except (ValueError, KeyError) as error:
            db.execute('''INSERT INTO library_errors(save_id,error,bundle_hash,last_seen) VALUES (?,?,?,?)
                          ON CONFLICT(save_id) DO UPDATE SET error=excluded.error,bundle_hash=excluded.bundle_hash,last_seen=excluded.last_seen''',
                       (save_id,str(error),bundle_hash,timestamp))
            return
        if problem:
            # A damaged/missing sibling must not hide individually verified slots.
            db.execute('''INSERT INTO library_errors(save_id,error,bundle_hash,last_seen) VALUES (?,?,?,?)
                          ON CONFLICT(save_id) DO UPDATE SET error=excluded.error,bundle_hash=excluded.bundle_hash,last_seen=excluded.last_seen''',
                       (save_id,problem,bundle_hash,timestamp))
        else:
            db.execute('DELETE FROM library_errors WHERE save_id=?', (save_id,))
        if not slots:
            return
        existing = db.execute('SELECT title FROM library_games WHERE backend=? AND game_key=?',(backend,game_key)).fetchone()
        if not getattr(plugin_for(bundle.save_id,backend),'library_slots',None):
            title = common_game_title([existing['title'],title] if existing else [title])
        db.execute('''INSERT INTO library_games(backend,game_key,title,icon_hash) VALUES (?,?,?,?)
                      ON CONFLICT(backend,game_key) DO UPDATE SET title=excluded.title,icon_hash=COALESCE(excluded.icon_hash,icon_hash)''',
                   (backend,game_key,title,hashlib.sha256(record['icon_model']).hexdigest() if record.get('icon_model') else None))
        game_id = db.execute('SELECT id FROM library_games WHERE backend=? AND game_key=?', (backend,game_key)).fetchone()[0]
        for slot in slots:
            if not slot.files or any(not isinstance(value,bytes) for value in slot.files.values()):
                raise ValueError('Game plugin returned an invalid library slot.')
            content_hash = self.objects.manifest({'files':[
                dict(name=name, object=self.objects.put(data)) for name,data in sorted(slot.files.items())]})
            db.execute('INSERT OR IGNORE INTO library_slots(game_id,slot_key,number) VALUES (?,?,?)', (game_id,slot.key,slot.number))
            entry = db.execute('SELECT * FROM library_slots WHERE game_id=? AND slot_key=?', (game_id,slot.key)).fetchone()
            # A newly supported game can enrich an existing whole-save lineage
            # without creating another save or changing its selected version.
            if slot.number is not None and entry['number'] is None:
                db.execute('UPDATE library_slots SET number=? WHERE id=?', (slot.number,entry['id']))
            source = db.execute('SELECT content_hash FROM library_sources WHERE save_id=? AND slot_id=?', (save_id,entry['id'])).fetchone()
            current = db.execute('SELECT * FROM library_versions WHERE id=?', (entry['current_version_id'],)).fetchone()
            version = db.execute('SELECT * FROM library_versions WHERE slot_id=? AND content_hash=? ORDER BY id DESC LIMIT 1',
                                 (entry['id'],content_hash)).fetchone()
            if version is not None and (slot.playtime_seconds is not None or slot.number is not None):
                db.execute('UPDATE library_versions SET label=?,playtime_seconds=COALESCE(?,playtime_seconds) WHERE id=?',
                           (slot.label,slot.playtime_seconds,version['id']))
            source_changed = source is not None and source['content_hash']!=content_hash
            if source is None or source_changed or version is None:
                if version is None:
                    key = db.execute('''INSERT INTO library_versions(slot_id,content_hash,bundle_hash,label,playtime_seconds,source_save_id,created)
                                        VALUES (?,?,?,?,?,?,?)''',
                                     (entry['id'],content_hash,bundle_hash,slot.label,slot.playtime_seconds,save_id,timestamp)).lastrowid
                else:
                    key = version['id']
                # First discovery prefers the greatest known playtime. Afterwards
                # an actual source change wins, including a deliberate earlier save.
                promote = current is None or source_changed or (
                    source is None and
                    slot.playtime_seconds is not None and
                    (current['playtime_seconds'] is None or slot.playtime_seconds>current['playtime_seconds']))
                if promote:
                    db.execute('UPDATE library_slots SET current_version_id=? WHERE id=?', (key,entry['id']))
            db.execute('''INSERT INTO library_sources(save_id,slot_id,content_hash,last_seen) VALUES (?,?,?,?)
                          ON CONFLICT(save_id,slot_id) DO UPDATE SET content_hash=excluded.content_hash,last_seen=excluded.last_seen,present=1''',
                       (save_id,entry['id'],content_hash,timestamp))

    def prune(self, db, slot_id):
        previous = db.execute('SELECT previous_versions FROM library_settings WHERE id=1').fetchone()[0]
        current = db.execute('SELECT current_version_id FROM library_slots WHERE id=?',(slot_id,)).fetchone()[0]
        active_hashes = {row[0] for row in db.execute('''SELECT o.content_hash FROM library_sources o
            JOIN saves s ON s.id=o.save_id JOIN cards c ON c.id=s.card_id JOIN locations l ON l.id=c.location_id
            WHERE o.slot_id=? AND o.present=1 AND s.present=1 AND c.present=1 AND l.deleted=0''',(slot_id,))}
        history = 0
        for row in db.execute('SELECT id,content_hash FROM library_versions WHERE slot_id=? ORDER BY id DESC',(slot_id,)).fetchall():
            if row['id']==current or row['content_hash'] in active_hashes:
                continue
            if history>=previous:
                db.execute('DELETE FROM library_versions WHERE id=?',(row['id'],))
            history += 1

    def prune_all(self, db):
        for row in db.execute('SELECT id FROM library_slots').fetchall():
            self.prune(db,row['id'])

    def inventory(self, db=None):
        if db is None:
            with self.db.connect() as db:
                db.execute('BEGIN')
                return self.inventory(db)
        games = [dict(row) for row in db.execute('SELECT * FROM library_games ORDER BY backend,title,game_key')]
        indexed_versions = {row[0] for row in db.execute('SELECT version_id FROM library_indexed_versions')}
        slots = [dict(row) for row in db.execute('SELECT * FROM library_slots ORDER BY number,slot_key')]
        versions = [dict(row) for row in db.execute('''SELECT v.*,d.name device,c.filename,s.directory FROM library_versions v
            JOIN saves s ON s.id=v.source_save_id JOIN cards c ON c.id=s.card_id
            JOIN locations l ON l.id=c.location_id JOIN devices d ON d.id=l.device_id ORDER BY v.id DESC''')]
        sources = [dict(row) for row in db.execute('''SELECT o.*,d.name device,c.filename,s.directory,l.deleted,
            o.present AND s.present AND c.present AND l.deleted=0 AND l.status='online' available
            FROM library_sources o JOIN saves s ON s.id=o.save_id JOIN cards c ON c.id=s.card_id
            JOIN locations l ON l.id=c.location_id JOIN devices d ON d.id=l.device_id ORDER BY d.name,c.filename''')]
        for slot in slots:
            slot['versions'] = [v for v in versions if v['slot_id']==slot['id']]
            slot['sources'] = [s for s in sources if s['slot_id']==slot['id']]
            slot['current'] = next((v for v in slot['versions'] if v['id']==slot['current_version_id']),None)
        for game in games:
            game['serial'] = game_serial(game['game_key'])
            game['slots'] = [slot for slot in slots if slot['game_id']==game['id']]
            distinct = {}
            for lineage in game['slots']:
                for version in lineage['versions']:
                    # Keep original whole-save backups in history without listing
                    # them again beside the individual saves extracted from them.
                    if lineage['number'] is None and lineage['slot_key'].endswith(':whole') and version['id'] in indexed_versions:
                        continue
                    entry = distinct.setdefault(version['content_hash'],{**version,'sources':[],'original_slots':[]})
                    if lineage['number'] is not None and lineage['number'] not in entry['original_slots']:
                        entry['original_slots'].append(lineage['number'])
                    for source in lineage['sources']:
                        if source['content_hash']==version['content_hash']:
                            origin = {**source,'original_slot':lineage['number']}
                            if origin not in entry['sources']:
                                entry['sources'].append(origin)
            game['saves'] = sorted(distinct.values(),key=lambda entry:entry['id'],reverse=True)
        errors = [dict(row) for row in db.execute('''SELECT e.save_id,e.error,e.last_seen,l.backend,d.name device,c.filename,s.directory
            FROM library_errors e JOIN saves s ON s.id=e.save_id JOIN cards c ON c.id=s.card_id
            JOIN locations l ON l.id=c.location_id JOIN devices d ON d.id=l.device_id WHERE l.deleted=0''')]
        previous = db.execute('SELECT previous_versions FROM library_settings WHERE id=1').fetchone()[0]
        return dict(games=games,errors=errors,previous_versions=previous)

    def game_settings(self, game_id, body):
        with self.service.lock:
            old = self.db.one('SELECT * FROM library_games WHERE id=?', (game_id,))
            ports = body.get('card_ports', old['card_ports'])
            note = body.get('card_port_note', old['card_port_note'])
            if ports not in ('unknown', 'port1', 'both'):
                raise ValueError('Choose Port 1 only, Both ports or Unknown.')
            if not isinstance(note, str) or (len(note)>1000 and note!=old['card_port_note']) or any(c in note for c in '\0\r'):
                raise ValueError('Card-port notes must be text of at most 1,000 characters.')
            with self.db.connect() as db:
                db.execute('UPDATE library_games SET card_ports=?,card_port_note=? WHERE id=?',
                           (ports,note.strip(),game_id))
        return self.db.one('SELECT * FROM library_games WHERE id=?', (game_id,))

    def settings(self, body):
        value = body.get('previous_versions')
        if type(value) is not int or not 0<=value<=100:
            raise ValueError('Previous versions must be between 0 and 100.')
        with self.service.lock, self.db.connect() as db:
            db.execute('UPDATE library_settings SET previous_versions=? WHERE id=1', (value,))
            self.prune_all(db)
        with self.service.lock:
            self.collect()
        return dict(previous_versions=value)

    def restore(self, slot_id, body):
        version_id = body.get('version_id')
        if type(version_id) is not int:
            raise ValueError('Choose a library version.')
        with self.service.lock, self.db.connect() as db:
            row = db.execute('SELECT * FROM library_versions WHERE id=? AND slot_id=?', (version_id,slot_id)).fetchone()
            if row is None:
                raise LookupError('Library version not found.')
            for file in self.objects.load(row['content_hash'])['files']:
                self.objects.get(file['object'])
            read_bundle(self.objects.load(row['bundle_hash']),self.objects.get)
            db.execute('UPDATE library_slots SET current_version_id=? WHERE id=?', (version_id,slot_id))
        return dict(slot_id=slot_id,current_version_id=version_id)

    def collect(self):
        """Called under the service lock after inventory/history commits."""
        with self.db.connect() as db:
            keys = {row[0] for row in db.execute('SELECT content_hash FROM library_versions UNION SELECT bundle_hash FROM library_versions UNION SELECT bundle_hash FROM library_errors WHERE bundle_hash IS NOT NULL')}
        keep = set(keys)
        for key in keys:
            keep.update(file['object'] for file in self.objects.load(key)['files'])
        for path in self.objects.root.iterdir():
            if re.fullmatch('[0-9a-f]{64}',path.name) and path.name not in keep:
                path.unlink()

    def download(self, version_id):
        with self.service.lock:
            version = self.db.one('SELECT * FROM library_versions WHERE id=?', (version_id,))
            slot = self.db.one('SELECT * FROM library_slots WHERE id=?', (version['slot_id'],))
            game = self.db.one('SELECT * FROM library_games WHERE id=?', (slot['game_id'],))
            content = self.objects.load(version['content_hash'])
            bundle = self.objects.load(version['bundle_hash'])
            keys = {file['object'] for manifest in (content,bundle) for file in manifest['files']}
            output = io.BytesIO()
            with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('save.json',json_bytes(dict(format='save-manager-library-v1',game=game,
                    slot_key=slot['slot_key'],slot_number=slot['number'],label=version['label'],
                    playtime_seconds=version['playtime_seconds'],content=content,bundle=bundle)))
                for key in sorted(keys):
                    archive.writestr('objects/'+key,self.objects.get(key))
            return output.getvalue()
