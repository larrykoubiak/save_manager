"""Whole-card synchronization using stable scans and per-device baselines."""

import os
from pathlib import Path
import time
import uuid

from save_core.card_sync import plan_card_copy
from save_core.backends import get_backend
from save_core.save_merge import digest, stable_read
from .service import now


class Sync:
    settle_seconds = 30
    scan_seconds = 300
    fresh_seconds = scan_seconds + 90

    def __init__(self, service):
        self.service = service
        self.db = service.db
        self.next_tick = 0

    def group(self, key):
        return self.db.one('SELECT * FROM sync_groups WHERE id=?', (key,))

    def members(self, group):
        return self.db.rows('''SELECT m.*,c.present,c.formatted,c.location_id,c.sha256 card_hash,c.content_hash,
                              l.status location_status,l.writable,l.device_id
                              FROM sync_members m JOIN cards c ON c.id=m.card_id
                              JOIN locations l ON l.id=c.location_id
                              WHERE m.group_id=? ORDER BY m.card_id''', (group['id'],))

    def configure(self, body):
        with self.service.lock:
            if 'directory' in body:
                raise ValueError('Sync now operates on whole memory cards. Choose cards and a group name.')
            label = body.get('name')
            if not isinstance(label, str) or not label.strip() or len(label)>100 or any(c in label for c in '\0\r\n'):
                raise ValueError('Give this memory-card group a name (1–100 characters).')
            ids = body.get('card_ids')
            if not isinstance(ids,list) or len(ids)<2 or len(ids)>32 or any(type(i) is not int for i in ids) or len(set(ids))!=len(ids):
                raise ValueError('Select between 2 and 32 different cards.')
            key = body.get('group_id')
            if key is not None:
                if type(key) is not int:
                    raise ValueError('Choose a memory-card sync group.')
                self.card_group(key)
            hashes, capacities, formatted = [], set(), 0
            backend_id = None
            for card_id in ids:
                card = self.db.one('SELECT * FROM cards WHERE id=? AND present=1', (card_id,))
                location = self.db.one('SELECT * FROM locations WHERE id=? AND deleted=0', (card['location_id'],))
                if not location['writable']:
                    raise ValueError('Destination location is read-only. Enable writes first.')
                if location['status']=='online':
                    card, location, _ = self.service._card(card_id, writable=True)
                if backend_id is not None and backend_id != location['backend']:
                    raise ValueError('Select cards from the same storage backend.')
                backend_id = location['backend']
                if card['formatted'] and location['status']=='online' and not card['content_hash']:
                    raise ValueError('Scan and format each selected card before enabling sync.')
                if card['formatted']:
                    formatted += 1
                    capacities.add(card['total_blocks'])
                hashes.append(card['content_hash'] if card['formatted'] else None)
            if not formatted:
                raise ValueError('Choose at least one formatted source card.')
            if len(capacities)>1:
                raise ValueError('Whole-card sync requires cards of the same capacity.')
            self.check_overlap(ids, key)
            with self.db.connect() as db:
                if key is None:
                    key = db.execute("INSERT INTO sync_groups(directory,label,scope,updated,backend) VALUES (?,?,'card',?,?)",
                                     ('@card-'+uuid.uuid4().hex,label.strip(),now(),backend_id)).lastrowid
                else:
                    db.execute("UPDATE sync_groups SET label=?,backend=?,enabled=1,head_hash=NULL,source_card_id=NULL,snapshot=NULL,status='settling',message='',next_scan=0,updated=? WHERE id=?",
                               (label.strip(),backend_id,now(),key))
                    db.execute('DELETE FROM sync_members WHERE group_id=?',(key,))
                for card_id, baseline in zip(ids,hashes):
                    db.execute('INSERT INTO sync_members(group_id,card_id,baseline_hash) VALUES (?,?,?)',(key,card_id,baseline))
                if len({value for value in hashes if value})>1:
                    db.execute("UPDATE sync_groups SET status='conflict',message='Cards differ. Choose the whole card to use first.' WHERE id=?",(key,))
            self.service.wake.set()
            return self.group(key)

    def card_group(self, key):
        group = self.group(key)
        if group['scope'] != 'card':
            raise ValueError('Per-game sync was replaced by memory-card sync. Create a card group.')
        return group

    def check_overlap(self, ids, key=None):
        placeholders = ','.join('?' for _ in ids)
        if self.db.rows(f'SELECT id FROM game_sync_links WHERE enabled=1 AND portable_card_id IN ({placeholders})',ids):
            raise ValueError('A PSP card already has per-game sync. Pause that link before using whole-card sync.')
        if self.db.rows(f"SELECT g.id FROM sync_groups g JOIN sync_members m ON m.group_id=g.id WHERE g.enabled=1 AND g.scope='card' AND m.card_id IN ({placeholders}) AND g.id!=?",
                        (*ids, key or -1)):
            raise ValueError('A selected card already belongs to another enabled sync group. Pause that group first.')

    def set_enabled(self, key, body):
        if type(body.get('enabled')) is not bool:
            raise ValueError('Enabled must be true or false.')
        with self.service.lock:
            group = self.card_group(key)
            if body['enabled']:
                members = self.members(group)
                if len(members) < 2:
                    raise ValueError('Choose at least two cards before resuming sync.')
                self.check_overlap([m['card_id'] for m in members],key)
            with self.db.connect() as db:
                db.execute('UPDATE sync_groups SET enabled=?,next_scan=0,updated=? WHERE id=?',(int(body['enabled']),now(),key))
                db.execute('UPDATE sync_members SET retry_after=0 WHERE group_id=?',(key,))
            self.service.wake.set()
            return self.group(key)

    def state(self, group, status, message):
        with self.db.connect() as db:
            db.execute('UPDATE sync_groups SET status=?,message=?,updated=? WHERE id=? AND (status!=? OR message!=?)',
                       (status,message,now(),group['id'],status,message))

    def member_state(self, group, member, status, message=''):
        with self.db.connect() as db:
            db.execute('UPDATE sync_members SET status=?,message=? WHERE group_id=? AND card_id=?',
                       (status,message,group['id'],member['card_id']))

    def available(self, member):
        return member['present'] and member['location_status']=='online'

    def settled(self, member):
        rows = self.db.rows('SELECT * FROM sync_observations WHERE card_id=?',(member['card_id'],))
        if not rows:
            return False
        observation = rows[0]
        at = time.time()
        return (observation['sha256']==member['card_hash'] and observation['checks']>=2
                and at-observation['since']>=self.settle_seconds
                and 0<=at-observation['checked']<=self.fresh_seconds)

    def capture(self, group, member):
        card, location, path = self.service._card(member['card_id'])
        data = stable_read(path)
        if digest(data)!=card['sha256']:
            raise ValueError('Source changed; waiting for another scan.')
        get_backend(location.get('backend', 'ps2')).validate(data, path)
        folder = self.service.data_dir/'sync'/str(group['id'])
        folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        snapshot = folder/(digest(data)+'.bin')
        if not snapshot.exists():
            temporary = folder/(uuid.uuid4().hex+'.tmp')
            try:
                with temporary.open('xb') as handle:
                    os.chmod(temporary,0o600)
                    handle.write(data);handle.flush();os.fsync(handle.fileno())
                os.replace(temporary,snapshot)
            finally:
                temporary.unlink(missing_ok=True)
        if digest(snapshot.read_bytes())!=digest(data):
            raise ValueError('Source snapshot verification failed.')
        with self.db.connect() as db:
            db.execute('UPDATE sync_groups SET head_hash=?,source_card_id=?,snapshot=?,status=\'pending\',message=\'\',updated=? WHERE id=?',
                       (member['content_hash'],member['card_id'],str(snapshot),now(),group['id']))
            db.execute('UPDATE sync_members SET baseline_hash=? WHERE group_id=? AND card_id=?',
                       (member['content_hash'],group['id'],member['card_id']))
            db.execute('UPDATE sync_members SET retry_after=0 WHERE group_id=?',(group['id'],))
        older = sorted((path for path in folder.glob('*.bin') if path != snapshot),
                       key=lambda path: path.stat().st_mtime_ns, reverse=True)
        for obsolete in older[2:]:
            obsolete.unlink()
        return self.group(group['id'])

    def retry(self, key):
        with self.service.lock:
            self.card_group(key)
            with self.db.connect() as db:
                db.execute('UPDATE sync_groups SET next_scan=0 WHERE id=?',(key,))
                db.execute('UPDATE sync_members SET retry_after=0 WHERE group_id=?',(key,))
            self.service.wake.set()
            return self.group(key)

    def resolve(self, key, body):
        with self.service.lock:
            group = self.card_group(key)
            members = self.members(group)
            selected = next((m for m in members if m['card_id']==body.get('source_card_id')),None)
            if not selected or not selected['formatted'] or not self.available(selected):
                raise ValueError('Choose an available copy in this sync group.')
            if selected['pending_scan'] or not self.settled(selected):
                raise ValueError('Waiting for two stable scans at least 30 seconds apart. Try again after the next scan.')
            # Choosing a source explicitly authorizes replacing the other known
            # copies. An offline copy edited since its last scan still conflicts.
            group = self.capture(group,selected)
            with self.db.connect() as db:
                db.execute('UPDATE sync_members SET retry_after=0 WHERE group_id=?',(key,))
                for member in members:
                    db.execute('UPDATE sync_members SET baseline_hash=? WHERE group_id=? AND card_id=?',
                               (member['content_hash'] if member['formatted'] else member['baseline_hash'],key,member['card_id']))
            self.service.wake.set()
            return group

    def enqueue_due(self):
        with self.service.lock:
            at = time.time()
            groups = self.db.rows("SELECT * FROM sync_groups WHERE enabled=1 AND scope='card' AND next_scan<=?",(at,))
            devices = set()
            for group in groups:
                devices.update(m['device_id'] for m in self.members(group))
            for key in devices:
                self.service.queue_scan(key,'sync')
            if groups:
                with self.db.connect() as db:
                    db.executemany('UPDATE sync_groups SET next_scan=? WHERE id=?',[(at+self.scan_seconds,g['id']) for g in groups])

    def tick(self):
        with self.service.lock:
            if time.monotonic()<self.next_tick:
                return
            self.next_tick = time.monotonic()+5
            for group in self.db.rows("SELECT * FROM sync_groups WHERE enabled=1 AND scope='card' ORDER BY id"):
                try:
                    self.process(group)
                except Exception as error:
                    self.state(group,'blocked',str(error))
            self.service.game_sync.tick()

    def process(self, group):
        members = self.members(group)
        if group['status']=='conflict':
            return
        available = [m for m in members if self.available(m)]
        # Never write any member while a visible card is changing, even when a
        # different member is the source. Reconcile all observations first.
        if any(not m['pending_scan'] and not self.settled(m) for m in available):
            self.state(group,'settling','Waiting for unchanged cards across two scans (30 seconds minimum).')
            return
        missing = [m for m in available if not m['pending_scan'] and not m['formatted'] and m['baseline_hash']]
        if missing:
            self.state(group,'blocked','A tracked card became unformatted. Format it before syncing; it will not be overwritten automatically.')
            return
        if any(m['formatted'] and not m['content_hash'] for m in available):
            self.state(group,'settling','Waiting for a fresh card scan.')
            return
        copies = [m for m in available if m['formatted']]
        if not group['head_hash'] and len({m['content_hash'] for m in copies})>1:
            self.state(group,'conflict','Cards differ. Choose the whole card to use first.')
            return
        changed = [m for m in copies if not m['pending_scan'] and m['content_hash']!=m['baseline_hash'] and m['content_hash']!=group['head_hash']]
        if changed:
            hashes = {m['content_hash'] for m in changed}
            if len(hashes)>1 or (group['head_hash'] and any(m['baseline_hash']!=group['head_hash'] for m in changed)):
                self.state(group,'conflict','Cards changed independently. Choose which whole card to keep.')
                return
            group = self.capture(group,changed[0])
        elif not group['head_hash']:
            if not copies:
                self.state(group,'pending','Waiting for a source device.')
                return
            if len({m['content_hash'] for m in copies})>1:
                self.state(group,'conflict','Cards differ. Choose the whole card to use first.')
                return
            group = self.capture(group,copies[0])
        pending = False
        blocked = []
        for member in self.members(group):
            if not self.available(member):
                pending = True
                self.member_state(group,member,'pending','Device or card is offline; update queued.')
                continue
            if member['pending_scan']:
                pending = True
                continue
            if member['formatted'] and member['content_hash']==group['head_hash']:
                with self.db.connect() as db:
                    db.execute("UPDATE sync_members SET baseline_hash=?,status='synced',message='' WHERE group_id=? AND card_id=?",
                               (group['head_hash'],group['id'],member['card_id']))
                continue
            if not member['writable']:
                message = 'Location is read-only.'
                blocked.append(message);self.member_state(group,member,'blocked',message)
                continue
            if member['retry_after']>time.time():
                blocked.append(member['message'])
                continue
            try:
                self.copy(group,member)
                pending = True  # Rescan confirms the completed write.
            except Exception as error:
                message = str(error)
                blocked.append(message);self.member_state(group,member,'blocked',message)
                with self.db.connect() as db:
                    db.execute('UPDATE sync_members SET retry_after=? WHERE group_id=? AND card_id=?',
                               (time.time()+300,group['id'],member['card_id']))
        if blocked:
            self.state(group,'blocked','; '.join(dict.fromkeys(blocked)))
        elif pending:
            self.state(group,'pending','Updates queued; waiting for devices or confirmation scans.')
        else:
            self.state(group,'synced','All selected copies match.')

    def copy(self, group, member):
        card, location, destination = self.service._card(member['card_id'],writable=True)
        source = Path(group['snapshot'])
        # Immutable source snapshot; the backend preserves destination encoding.
        if location['backend'] != group['backend']:
            raise ValueError('Sync destination uses a different storage backend.')
        if digest(get_backend(group['backend']).card_data(stable_read(source))) != group['head_hash']:
            raise ValueError('Sync snapshot failed its content integrity check.')
        plan = plan_card_copy(group['backend'],source,destination)
        if plan.destination_hash!=member['card_hash']:
            raise ValueError('Destination changed; waiting for another scan.')
        key = uuid.uuid4().hex
        with self.db.connect() as db:
            db.execute('''INSERT INTO transfers(id,source_card_id,destination_card_id,directory,destination_save,
                          source_hash,destination_hash,action,status,created) VALUES (?,?,?,?,?,?,?,'Automatic card sync','applying',?)''',
                       (key,group['source_card_id'],member['card_id'],group['label'],plan.destination_save,
                        plan.source_hash,plan.destination_hash,now()))
        try:
            backup = self.service.apply_plan(plan, card, location)
        except Exception as error:
            with self.db.connect() as db:
                db.execute("UPDATE transfers SET status='failed',finished=?,error=?,backup=? WHERE id=?",
                           (now(),str(error),getattr(error,'backup',None),key))
            raise
        # Update only after a verified write. A crash before this commit is also
        # recoverable: the next scan sees the accepted hash and acknowledges it.
        with self.db.connect() as db:
            db.execute("UPDATE transfers SET status='done',finished=?,backup=? WHERE id=?",(now(),str(backup) if backup else None,key))
            db.execute("UPDATE sync_members SET baseline_hash=?,pending_scan=1,status='pending',message='Written; awaiting scan.' WHERE group_id=? AND card_id=?",
                       (group['head_hash'],group['id'],member['card_id']))
        self.service.queue_scan(location['device_id'],'sync')
