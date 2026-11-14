"""Per-game PSP links backed by the existing shared-card sync groups."""

from datetime import datetime, timezone
from pathlib import Path
import time
import uuid

from save_core.game_sync import game_contents, game_fingerprint, plan_game_copy
from save_core.save_merge import stable_read, digest
from save_core.save_games import game_serial
from .service import now


class GameSync:
    def __init__(self, service):
        self.service, self.db = service, service.db

    def link(self, key):
        return self.db.one('SELECT * FROM game_sync_links WHERE id=?', (key,))

    def portable(self, link):
        return self.db.one('''SELECT c.*,c.id card_id,c.sha256 card_hash,l.device_id,l.status location_status,
            l.writable,l.backend,d.transport,d.root_path,l.path location_path
            FROM cards c JOIN locations l ON l.id=c.location_id JOIN devices d ON d.id=l.device_id
            WHERE c.id=? AND l.deleted=0 AND d.deleted=0''', (link['portable_card_id'],))

    def configure(self, body):
        with self.service.lock:
            serial, group_id, card_id = body.get('game_key'), body.get('group_id'), body.get('portable_card_id')
            if not isinstance(serial, str) or game_serial(serial) != serial:
                raise ValueError('Choose a game by its platform serial.')
            if type(group_id) is not int or type(card_id) is not int:
                raise ValueError('Choose a shared-card group and a PSP card.')
            group = self.service.sync.card_group(group_id)
            if group['backend'] != 'ps1' or not group['enabled']:
                raise ValueError('Choose an enabled PS1 shared-card group.')
            card = self.portable({'portable_card_id': card_id})
            if card['backend'] != 'ps1' or not card['filename'].lower().endswith('.vmp') or not card['writable']:
                raise ValueError('Choose a writable PSP VMP card.')
            if self.db.rows('''SELECT 1 FROM sync_members m JOIN sync_groups g ON g.id=m.group_id
                               WHERE g.enabled=1 AND m.card_id=?''', (card_id,)):
                raise ValueError('Remove the PSP card from whole-card sync before linking its game.')
            if self.db.rows('SELECT 1 FROM game_sync_links WHERE portable_card_id=?', (card_id,)):
                raise ValueError('This PSP card already has a per-game link.')
            if not group['snapshot'] or not game_contents(stable_read(Path(group['snapshot'])), serial):
                raise ValueError('The selected shared card does not contain this game yet.')
            preferred = body.get('initial_source')
            if preferred not in (None, 'shared', 'portable'):
                raise ValueError('Choose shared or portable as the starting copy.')
            game = self.db.rows("SELECT title FROM library_games WHERE backend='ps1' AND game_key=?", (serial,))
            label = game[0]['title'] if game else serial
            with self.db.connect() as db:
                key = db.execute('''INSERT INTO game_sync_links(game_key,label,group_id,portable_card_id,preferred,updated)
                                    VALUES (?,?,?,?,?,?)''', (serial,label,group_id,card_id,preferred,now())).lastrowid
            self.service.wake.set()
            return self.link(key)

    def update(self, key, body):
        with self.service.lock:
            self.link(key)
            with self.db.connect() as db:
                if 'enabled' in body:
                    if type(body['enabled']) is not bool:
                        raise ValueError('Enabled must be true or false.')
                    if body['enabled'] and db.execute('''SELECT 1 FROM sync_members m JOIN sync_groups g ON g.id=m.group_id
                        JOIN game_sync_links x ON x.portable_card_id=m.card_id WHERE x.id=? AND g.enabled=1''',(key,)).fetchone():
                        raise ValueError('This PSP card belongs to whole-card sync.')
                    db.execute('UPDATE game_sync_links SET enabled=? WHERE id=?',(int(body['enabled']),key))
                if 'source' in body:
                    if body['source'] not in ('shared','portable'):
                        raise ValueError('Choose shared or portable as the source.')
                    db.execute("UPDATE game_sync_links SET preferred=?,status='settling',message='' WHERE id=?",(body['source'],key))
                db.execute('UPDATE game_sync_links SET next_scan=0,retry_after=0,updated=? WHERE id=?',(now(),key))
            self.service.wake.set()
            return self.link(key)

    def state(self, link, status, message):
        with self.db.connect() as db:
            db.execute('UPDATE game_sync_links SET status=?,message=?,updated=? WHERE id=? AND (status!=? OR message!=?)',
                       (status,message,now(),link['id'],status,message))

    def enqueue_due(self):
        at = time.time()
        links = self.db.rows('SELECT * FROM game_sync_links WHERE enabled=1 AND next_scan<=?', (at,))
        devices = set()
        for link in links:
            try:
                devices.add(self.portable(link)['device_id'])
                devices.update(m['device_id'] for m in self.service.sync.members(self.service.sync.group(link['group_id'])))
            except LookupError:
                self.state(link,'blocked','A linked device or card was removed.')
        for device in devices:
            self.service.queue_scan(device,'game-sync')
        with self.db.connect() as db:
            db.executemany('UPDATE game_sync_links SET next_scan=? WHERE id=?',
                           [(at+self.service.sync.scan_seconds,link['id']) for link in links])

    def tick(self):
        for link in self.db.rows('SELECT * FROM game_sync_links WHERE enabled=1 ORDER BY id'):
            if link['retry_after'] > time.time():
                continue
            try:
                self.process(link)
            except Exception as error:
                self.state(link,'blocked',str(error))
                with self.db.connect() as db:
                    db.execute('UPDATE game_sync_links SET retry_after=? WHERE id=?',(time.time()+300,link['id']))

    def cached(self, portable):
        if portable['transport'] == 'ftp':
            path = self.service.data_dir/'ftp-cache'/str(portable['device_id'])/str(portable['location_id'])/portable['filename']
        else:
            from save_core.storage_paths import card_path
            path = card_path({**portable,'path':portable['location_path']}, portable['filename'])
        data = stable_read(path)
        if digest(data) != portable['sha256']:
            raise ValueError('Waiting for a fresh PSP card scan.')
        return data

    def shared_ready(self, group):
        # Offline copies can catch up later. Every visible copy must already
        # agree with the accepted snapshot before a game can use that snapshot.
        if (not group['enabled'] or not group['snapshot'] or not group['head_hash']
                or group['status'] not in ('synced', 'pending')):
            return False
        available = [member for member in self.service.sync.members(group)
                     if self.service.sync.available(member)]
        return bool(available) and all(
            member['formatted'] and not member['pending_scan']
            and member['content_hash'] == member['baseline_hash'] == group['head_hash']
            and self.service.sync.settled(member) for member in available)

    def process(self, link):
        group = self.service.sync.group(link['group_id'])
        portable = self.portable(link)
        if not portable['present'] or portable['location_status'] != 'online':
            self.state(link,'pending','PSP offline; game update queued.')
            return
        if not self.shared_ready(group):
            self.state(link,'pending','Waiting for the shared-card group to finish syncing.')
            return
        if link['pending_card_id']:
            card = self.db.one('SELECT * FROM cards WHERE id=?',(link['pending_card_id'],))
            if card['sha256'] != link['pending_hash'] and card['last_seen'] <= link['pending_since']:
                self.state(link,'pending','Written; awaiting confirmation scan.')
                return
            with self.db.connect() as db:
                db.execute('UPDATE game_sync_links SET pending_card_id=NULL,pending_hash=NULL,pending_since=NULL WHERE id=?',(link['id'],))
        if not self.service.sync.settled(portable):
            self.state(link,'settling','Waiting for a stable PSP card scan.')
            return
        snapshot = Path(group['snapshot'])
        shared_data = stable_read(snapshot)
        from save_core.backends import get_backend
        if digest(get_backend('ps1').card_data(shared_data)) != group['head_hash']:
            raise ValueError('Shared-card snapshot integrity check failed.')
        shared_hash = game_fingerprint(shared_data,link['game_key'])
        portable_hash = game_fingerprint(self.cached(portable),link['game_key'])
        direction = link['preferred']
        if direction is None and shared_hash != portable_hash:
            if link['shared_baseline'] is None or link['portable_baseline'] is None:
                self.state(link,'conflict','Starting saves differ. Choose shared or PSP saves.')
                return
            shared_changed = shared_hash != link['shared_baseline']
            portable_changed = portable_hash != link['portable_baseline']
            if shared_changed and portable_changed:
                self.state(link,'conflict','This game changed on both devices. Choose which saves to keep.')
                return
            direction = 'shared' if shared_changed else 'portable' if portable_changed else None
            if direction is None:
                self.state(link,'conflict','Game copies differ; choose the source to use.')
                return
        if shared_hash == portable_hash and not link['initialize_portable']:
            self.accept(link,shared_hash)
            self.state(link,'synced','Game saves match.')
            return
        if direction is None:
            direction = 'shared'
        if direction == 'shared':
            source,source_id = snapshot,group['source_card_id']
            card,location,destination = self.service._card(portable['id'],writable=True)
            expected = shared_hash
            initialize = bool(link['initialize_portable'])
        else:
            _,_,source = self.service._card(portable['id'])
            candidates = [m for m in self.service.sync.members(group)
                          if self.service.sync.available(m) and m['writable'] and not m['pending_scan']
                          and m['content_hash']==group['head_hash'] and self.service.sync.settled(m)]
            if not candidates:
                self.state(link,'pending','Waiting for an available shared card.')
                return
            # Prefer a mounted target; whole-card sync distributes it to the rest.
            candidates.sort(key=lambda m: self.db.one('SELECT transport FROM devices WHERE id=?',(m['device_id'],))['transport']=='ftp')
            card,location,destination = self.service._card(candidates[0]['card_id'],writable=True)
            source_id,expected,initialize = portable['id'],portable_hash,False
        plan = plan_game_copy(source,destination,link['game_key'],initialize_portable=initialize)
        if direction == 'shared' and game_fingerprint(stable_read(source),link['game_key']) != shared_hash:
            raise ValueError('Shared source changed during planning.')
        key=uuid.uuid4().hex
        with self.db.connect() as db:
            db.execute('''INSERT INTO transfers(id,source_card_id,destination_card_id,directory,destination_save,
                source_hash,destination_hash,action,status,created) VALUES (?,?,?,?,?,?,?,'Automatic game sync','applying',?)''',
                (key,source_id,card['id'],link['game_key'],link['game_key'],plan.source_hash,plan.destination_hash,now()))
        try:
            backup=self.service.apply_plan(plan,card,location)
        except Exception as error:
            with self.db.connect() as db:
                db.execute("UPDATE transfers SET status='failed',finished=?,error=?,backup=? WHERE id=?",
                           (now(),str(error),getattr(error,'backup',None),key))
            raise
        with self.db.connect() as db:
            db.execute("UPDATE transfers SET status='done',finished=?,backup=? WHERE id=?",(now(),str(backup) if backup else None,key))
            if direction == 'portable' and plan.action != 'Already identical':
                # Other game links must wait until inventory observes this
                # write, even though its old cached hash still matches the head.
                db.execute('UPDATE sync_members SET pending_scan=1 WHERE group_id=? AND card_id=?',
                           (group['id'],card['id']))
            db.execute('''UPDATE game_sync_links SET shared_baseline=?,portable_baseline=?,preferred=NULL,
                initialize_portable=?,pending_card_id=?,pending_hash=?,pending_since=?,status='pending',
                message='Written; awaiting confirmation scan.',updated=? WHERE id=?''',
                (expected,expected,0 if direction=='shared' else link['initialize_portable'],card['id'],digest(plan.candidate),datetime.now(timezone.utc).isoformat(),now(),link['id']))
        if direction == 'portable':
            self.service.sync.state(group,'pending','PSP game update written; awaiting shared-card scan.')
        self.service.queue_scan(location['device_id'],'game-sync')

    def accept(self, link, fingerprint):
        with self.db.connect() as db:
            db.execute('''UPDATE game_sync_links SET shared_baseline=?,portable_baseline=?,preferred=NULL,
                          updated=? WHERE id=?''',(fingerprint,fingerprint,now(),link['id']))
