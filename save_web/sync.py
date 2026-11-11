"""Opt-in save synchronization using stable scans and per-card baselines."""

import os
from pathlib import Path
import time
import uuid

from save_core.save_copy import apply_copy
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
        return self.db.rows('''SELECT m.*,c.present,c.formatted,c.location_id,c.sha256 card_hash,
                              l.status location_status,l.writable,l.device_id,
                              s.sha256 save_hash,s.present save_present
                              FROM sync_members m JOIN cards c ON c.id=m.card_id
                              JOIN locations l ON l.id=c.location_id
                              LEFT JOIN saves s ON s.card_id=c.id AND s.directory=?
                              WHERE m.group_id=? ORDER BY m.card_id''', (group['directory'],group['id']))

    def configure(self, body):
        with self.service.lock:
            directory = body.get('directory')
            if not isinstance(directory,str) or not directory or '\0' in directory or len(directory)>255:
                raise ValueError('Choose one save identity to synchronize.')
            ids = body.get('card_ids')
            if not isinstance(ids,list) or len(ids)<2 or len(ids)>32 or any(type(i) is not int for i in ids) or len(set(ids))!=len(ids):
                raise ValueError('Select between 2 and 32 different cards.')
            hashes = []
            backend_id = None
            for key in ids:
                card, location, _ = self.service._card(key, writable=True)
                if backend_id is not None and backend_id != location['backend']:
                    raise ValueError('Select cards from the same storage backend.')
                backend_id = location['backend']
                if not card['formatted']:
                    raise ValueError('Format each selected card before enabling sync.')
                saves = self.db.rows('SELECT sha256 FROM saves WHERE card_id=? AND directory=? AND present=1', (key,directory))
                hashes.append(saves[0]['sha256'] if saves else None)
            if not any(hashes):
                raise ValueError('At least one selected card must contain this save.')
            with self.db.connect() as db:
                db.execute('''INSERT INTO sync_groups(directory,updated,backend) VALUES (?,?,?)
                              ON CONFLICT(backend,directory) DO UPDATE SET enabled=1,head_hash=NULL,source_card_id=NULL,
                              snapshot=NULL,status='settling',message='',next_scan=0,updated=excluded.updated''', (directory,now(),backend_id))
                key = db.execute('SELECT id FROM sync_groups WHERE directory=? AND backend=?',(directory,backend_id)).fetchone()[0]
                db.execute('DELETE FROM sync_members WHERE group_id=?',(key,))
                for card_id, baseline in zip(ids,hashes):
                    db.execute('INSERT INTO sync_members(group_id,card_id,baseline_hash) VALUES (?,?,?)',(key,card_id,baseline))
                if len(set(h for h in hashes if h))>1:
                    db.execute("UPDATE sync_groups SET status='conflict',message='Copies differ. Choose the copy to use first.' WHERE id=?",(key,))
            self.service.wake.set()
            return self.group(key)

    def set_enabled(self, key, body):
        if type(body.get('enabled')) is not bool:
            raise ValueError('Enabled must be true or false.')
        with self.service.lock:
            if body['enabled'] and len(self.members(self.group(key))) < 2:
                raise ValueError('Choose at least two cards before resuming sync.')
            self.group(key)
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
                       (member['save_hash'],member['card_id'],str(snapshot),now(),group['id']))
            db.execute('UPDATE sync_members SET baseline_hash=? WHERE group_id=? AND card_id=?',
                       (member['save_hash'],group['id'],member['card_id']))
            db.execute('UPDATE sync_members SET retry_after=0 WHERE group_id=?',(group['id'],))
        return self.group(group['id'])

    def retry(self, key):
        with self.service.lock:
            self.group(key)
            with self.db.connect() as db:
                db.execute('UPDATE sync_groups SET next_scan=0 WHERE id=?',(key,))
                db.execute('UPDATE sync_members SET retry_after=0 WHERE group_id=?',(key,))
            self.service.wake.set()
            return self.group(key)

    def resolve(self, key, body):
        with self.service.lock:
            group = self.group(key)
            members = self.members(group)
            selected = next((m for m in members if m['card_id']==body.get('source_card_id')),None)
            if not selected or not selected['save_present'] or not self.available(selected):
                raise ValueError('Choose an available copy in this sync group.')
            if not self.settled(selected):
                raise ValueError('Waiting for two stable scans at least 30 seconds apart. Try again after the next scan.')
            # Choosing a source explicitly authorizes replacing the other known
            # copies. An offline copy edited since its last scan still conflicts.
            group = self.capture(group,selected)
            with self.db.connect() as db:
                db.execute('UPDATE sync_members SET retry_after=0 WHERE group_id=?',(key,))
                for member in members:
                    db.execute('UPDATE sync_members SET baseline_hash=? WHERE group_id=? AND card_id=?',
                               (member['save_hash'] if member['save_present'] else member['baseline_hash'],key,member['card_id']))
            self.service.wake.set()
            return group

    def enqueue_due(self):
        with self.service.lock:
            at = time.time()
            groups = self.db.rows('SELECT * FROM sync_groups WHERE enabled=1 AND next_scan<=?',(at,))
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
            for group in self.db.rows('SELECT * FROM sync_groups WHERE enabled=1 ORDER BY id'):
                try:
                    self.process(group)
                except Exception as error:
                    self.state(group,'blocked',str(error))

    def process(self, group):
        members = self.members(group)
        if group['status']=='conflict':
            return
        available = [m for m in members if self.available(m)]
        # Never write any member while a visible card is changing, even when a
        # different member is the source. Reconcile all observations first.
        if any(not self.settled(m) for m in available):
            self.state(group,'settling','Waiting for unchanged cards across two scans (30 seconds minimum).')
            return
        missing = [m for m in available if not m['save_present'] and m['baseline_hash']]
        if missing:
            self.state(group,'blocked','A tracked save was removed. It will not be recreated automatically; edit the sync group to include it again.')
            return
        copies = [m for m in available if m['save_present']]
        if not group['head_hash'] and len({m['save_hash'] for m in copies})>1:
            self.state(group,'conflict','Copies differ. Choose the copy to use first.')
            return
        changed = [m for m in copies if m['save_hash']!=m['baseline_hash'] and m['save_hash']!=group['head_hash']]
        if changed:
            hashes = {m['save_hash'] for m in changed}
            if len(hashes)>1 or (group['head_hash'] and any(m['baseline_hash']!=group['head_hash'] for m in changed)):
                self.state(group,'conflict','Copies changed independently. Choose which copy to keep.')
                return
            group = self.capture(group,changed[0])
        elif not group['head_hash']:
            if not copies:
                self.state(group,'pending','Waiting for a source device.')
                return
            if len({m['save_hash'] for m in copies})>1:
                self.state(group,'conflict','Copies differ. Choose the copy to use first.')
                return
            group = self.capture(group,copies[0])
        pending = False
        blocked = []
        for member in self.members(group):
            if not self.available(member):
                pending = True
                self.member_state(group,member,'pending','Device or card is offline; update queued.')
                continue
            if member['save_present'] and member['save_hash']==group['head_hash']:
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
        _, location, destination = self.service._card(member['card_id'],writable=True)
        source = Path(group['snapshot'])
        # The captured card is immutable. Only the selected directory is copied.
        if location['backend'] != group['backend']:
            raise ValueError('Sync destination uses a different storage backend.')
        plan = get_backend(group['backend']).plan_copy(source,{'save':group['directory']},destination,'replace')
        if plan.destination_hash!=member['card_hash']:
            raise ValueError('Destination changed; waiting for another scan.')
        key = uuid.uuid4().hex
        with self.db.connect() as db:
            db.execute('''INSERT INTO transfers(id,source_card_id,destination_card_id,directory,destination_save,
                          source_hash,destination_hash,action,status,created) VALUES (?,?,?,?,?,?,?,'Automatic sync','applying',?)''',
                       (key,group['source_card_id'],member['card_id'],group['directory'],plan.destination_save,
                        plan.source_hash,plan.destination_hash,now()))
        try:
            backup = apply_copy(plan,self.service.data_dir/'backups')
        except Exception as error:
            with self.db.connect() as db:
                db.execute("UPDATE transfers SET status='failed',finished=?,error=?,backup=? WHERE id=?",
                           (now(),str(error),getattr(error,'backup',None),key))
            raise
        # Update only after a verified write. A crash before this commit is also
        # recoverable: the next scan sees the accepted hash and acknowledges it.
        with self.db.connect() as db:
            db.execute("UPDATE transfers SET status='done',finished=?,backup=? WHERE id=?",(now(),str(backup) if backup else None,key))
            db.execute("UPDATE sync_members SET baseline_hash=?,status='pending',message='Written; awaiting scan.' WHERE group_id=? AND card_id=?",
                       (group['head_hash'],group['id'],member['card_id']))
        self.service.queue_scan(location['device_id'],'sync')
