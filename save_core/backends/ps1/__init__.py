"""PS1 raw-card backend: inventory, verified copies and deletion."""

from dataclasses import replace
from pathlib import Path
import struct
import unicodedata

from ..base import BackendCapabilities, SaveBundle, SaveFile
from .card import SIZE, Card, blank_card


def is_erased(data):
    return len(data)==SIZE and (data==b'\xff'*SIZE or data==bytes(SIZE))


def raw_image(data):
    """Read PSP VMP containers without editing their signed header."""
    if data[:4]==b'\0PMV':
        if len(data)!=SIZE+128 or struct.unpack_from('<I',data,4)[0]!=128:
            raise ValueError('Invalid PSP VMP container size or header.')
        return data[128:]
    return data


class PS1Backend:
    backend_id = 'ps1'
    suffixes = ('.mcr','.mcd','.srm','.bin','.mem','.vm1','.vmc','.vmp')
    capabilities = BackendCapabilities(capacity=True,icons=True,delete=True,file_updates=True)

    def validate(self, data, name):
        Card(raw_image(data))

    def _read(self, path, record=None, *, writable=False):
        from save_core.save_merge import stable_read
        path = Path(path).resolve()
        data = stable_read(path)
        stat = path.stat()
        if record and record.get('card_version') and record['card_version']!=(stat.st_size,stat.st_mtime_ns):
            raise ValueError('Card changed since its scan. Scan and preview again.')
        raw = raw_image(data)
        if writable and (raw is not data or path.suffix.lower()=='.vmp'):
            raise ValueError('PSP VMP cards are read-only until signing support is added.')
        if is_erased(raw):
            raise ValueError('PS1 card is unformatted. Format it in the emulator first.')
        return path,data,Card(raw)

    @staticmethod
    def _bundle(card, save_id):
        if save_id not in card.entries:
            raise ValueError('PS1 save is missing. Scan and preview again.')
        entry = card.entries[save_id]
        return SaveBundle(save_id,(SaveFile('data',card.payload(entry)),),card.metadata(entry))

    def read_save(self, path, save_id):
        _,_,card = self._read(path)
        return self._bundle(card,save_id)

    def snapshot_location(self, location):
        from game_plugins import plugin_for, slot_data
        from save_core.save_merge import digest, stable_read
        from save_core.storage_paths import location_root, card_files
        root = location_root(location)
        suffix = location['suffix']
        def paths():
            return card_files(location, self.suffixes if suffix=='*' else (suffix,))
        selected = paths()
        versions = {p:(p.stat().st_size,p.stat().st_mtime_ns) for p in selected}
        result = []
        for path in selected:
            filename = path.relative_to(root).as_posix()
            data = stable_read(path)
            try:
                raw = raw_image(data)
                card = None if is_erased(raw) else Card(raw)
            except ValueError as error:
                raise ValueError(f'{filename}: {error}') from error
            records = []
            for entry in card.entries.values() if card else ():
                bundle = self._bundle(card,entry.name)
                payload = bundle.file_data()['data']
                title,icon = '',None
                if payload[:2]==b'SC':
                    title = unicodedata.normalize('NFKC',payload[4:68].split(b'\0',1)[0].decode('shift_jis',errors='replace'))
                    title = ' '.join(title.split())
                    if payload[2] in (0x11,0x12,0x13):
                        icon = b'PS1I'+payload[:128*(1+payload[2]-0x10)]
                record = dict(save=entry.name,title=title or entry.name,card=filename,
                              updated=None,size_blocks=len(entry.blocks)*8,sha256=digest(payload),
                              slots=[],icon_model=icon,summary='')
                plugin = plugin_for(entry.name,backend_id=self.backend_id)
                if callable(getattr(plugin,'slots',None)):
                    record['slots'] = plugin.slots(entry.name,slot_data(plugin,entry.name,bundle.file_data()))
                if callable(getattr(plugin,'summary',None)):
                    record.update(plugin.summary(entry.name,slot_data(plugin,entry.name,bundle.file_data())))
                records.append(record)
            stat = path.stat()
            if versions[path]!=(stat.st_size,stat.st_mtime_ns):
                raise ValueError('PS1 card changed during scan; retry when it is idle.')
            for record in records:
                record['card_version'] = (stat.st_size,stat.st_mtime_ns)
            result.append(dict(filename=filename,sha256=digest(data),size=stat.st_size,mtime_ns=stat.st_mtime_ns,
                               formatted=card is not None,total_blocks=120 if card else None,
                               free_blocks=len(card.free_blocks())*8 if card else None,saves=records))
        if paths()!=selected:
            raise ValueError('PS1 location changed during scan; retry when it is idle.')
        return result

    def _plan(self, source, source_data, destination, original, card, save_id, bundle, action):
        from save_core.save_copy import CopyPlan
        from save_core.save_merge import digest
        before = Card(original)
        if save_id in card.entries:
            card.delete(save_id)
        if bundle is not None:
            card.put(save_id,bundle.file_data()['data'],bundle.metadata)
        candidate = bytes(card.data)
        checked = Card(candidate)
        unchanged = set(before.entries)-{save_id}
        if set(checked.entries)!=(unchanged|({save_id} if bundle is not None else set())):
            raise ValueError('PS1 candidate save list does not match the plan.')
        if any(before.signature(name)!=checked.signature(name) for name in unchanged):
            raise ValueError('PS1 candidate changed an unrelated save.')
        if bundle is not None:
            if checked.payload(checked.entries[save_id])!=bundle.file_data()['data']:
                raise ValueError('PS1 candidate does not match the selected save.')
            expected = Card(blank_card())
            expected.put(save_id,bundle.file_data()['data'],bundle.metadata)
            if expected.signature(save_id)[:2]!=checked.signature(save_id)[:2]:
                raise ValueError('PS1 save metadata changed unexpectedly.')
        # Preserve control sectors, unrelated directory records and free data.
        changed_blocks = set(before.entries[save_id].blocks) if save_id in before.entries else set()
        if save_id in checked.entries:
            changed_blocks.update(checked.entries[save_id].blocks)
        allowed = set(changed_blocks)
        written_blocks = checked.entries[save_id].blocks if save_id in checked.entries else ()
        for block in written_blocks:
            allowed.update(before.remapped.get(block*64+sector,block*64+sector) for sector in range(64))
        for sector in range(SIZE//128):
            if sector not in allowed and original[sector*128:(sector+1)*128]!=candidate[sector*128:(sector+1)*128]:
                raise ValueError('PS1 candidate changed data outside the selected save.')
        return CopyPlan(source,destination,digest(source_data),digest(original),save_id,save_id,action,candidate,original)

    def plan_copy(self, source, record, destination, conflict='stop'):
        from game_plugins import plugin_for, slot_data
        from save_core.save_copy import CopyPlan
        from save_core.save_merge import digest
        if conflict not in ('stop','replace','keep_both'):
            raise ValueError('Unknown PS1 copy conflict policy.')
        source,source_data,source_card = self._read(source,record)
        destination,original,card = self._read(destination,writable=True)
        if source.samefile(destination):
            raise ValueError('Choose a different destination card.')
        save_id = record['save']
        bundle = self._bundle(source_card,save_id)
        plugin = plugin_for(save_id,backend_id=self.backend_id)
        if callable(getattr(plugin,'validate_save',None)):
            plugin.validate_save(save_id,slot_data(plugin,save_id,bundle.file_data()))
        if save_id in card.entries:
            if card.signature(save_id)==source_card.signature(save_id):
                return CopyPlan(source,destination,digest(source_data),digest(original),save_id,save_id,'Already identical',b'',original)
            if conflict=='keep_both':
                if not getattr(plugin,'card_slots',False):
                    raise ValueError('Keeping both saves is unsupported for this PS1 game.')
                slot = next((n for n in range(1,plugin.slot_count+1)
                             if plugin.prepare_slot(bundle,n).save_id not in card.entries),None)
                if slot is None:
                    raise ValueError('No unused game save slots remain on this card.')
                return self.plan_slot_copy(source,record,plugin.slot_number(save_id),destination,slot)
            if conflict!='replace':
                raise ValueError('A different PS1 save already uses this filename. Choose replacement explicitly.')
        action = 'Replace existing save' if save_id in card.entries else 'Copy'
        return self._plan(source,source_data,destination,original,card,save_id,bundle,action)

    def plan_delete(self, path, record):
        path,data,card = self._read(path,record,writable=True)
        self._bundle(card,record['save'])
        return self._plan(path,data,path,data,card,record['save'],None,'Delete save')

    def plan_slot_copy(self, source, record, source_slot, destination, destination_slot, conflict='stop'):
        from game_plugins import plugin_for, slot_data
        from save_core.save_copy import CopyPlan
        from save_core.save_merge import digest
        if conflict not in ('stop','replace'):
            raise ValueError('Slot conflicts support stop or explicit replacement.')
        plugin = plugin_for(record['save'],backend_id=self.backend_id)
        card_slots = getattr(plugin,'card_slots',False)
        if not card_slots and not callable(getattr(plugin,'copy_slot',None)):
            raise ValueError('No PS1 game plugin supports individual slots for this save yet.')
        source,source_data,source_card = self._read(source,record)
        destination,original,card = self._read(destination,writable=True)
        source_bundle = self._bundle(source_card,record['save'])
        if card_slots:
            if type(source_slot) is not int or source_slot!=plugin.slot_number(record['save']):
                raise ValueError('Source slot does not match this save.')
            updated = plugin.prepare_slot(source_bundle,destination_slot)
            save_id = updated.save_id
            if source.samefile(destination) and save_id==record['save']:
                raise ValueError('Choose a different destination slot.')
            if save_id in card.entries:
                if card.payload(card.entries[save_id])==updated.file_data()['data']:
                    return CopyPlan(source,destination,digest(source_data),digest(original),record['save'],save_id,
                                    'Already identical',b'',original,source_slot=source_slot,destination_slot=destination_slot)
                if conflict!='replace':
                    raise ValueError(f'Destination slot {destination_slot} is occupied. Choose replacement explicitly.')
            plan = self._plan(source,source_data,destination,original,card,save_id,updated,'Copy save slot')
            return replace(plan,source_save=record['save'],source_slot=source_slot,destination_slot=destination_slot)
        target = self._bundle(card,record['save'])
        result = plugin.copy_slot(record['save'],slot_data(plugin,record['save'],source_bundle.file_data()),source_slot,
                                  slot_data(plugin,record['save'],target.file_data()),destination_slot,replace=conflict=='replace')
        payload = result['data'] if isinstance(result,dict) else result
        if not isinstance(payload,bytes) or len(payload)!=len(target.files[0].data):
            raise ValueError('A PS1 slot update must preserve the allocated save size.')
        if callable(getattr(plugin,'slots',None)):
            checked_payload = {'data':payload} if isinstance(result,dict) else payload
            if any(slot['state']=='invalid' for slot in plugin.slots(record['save'],checked_payload)):
                raise ValueError('The updated PS1 save contains invalid slots.')
        if payload==target.files[0].data:
            return CopyPlan(source,destination,digest(source_data),digest(original),record['save'],record['save'],'Already identical',b'',original)
        updated = replace(target,files=(replace(target.files[0],data=payload),))
        return replace(self._plan(source,source_data,destination,original,card,record['save'],updated,'Copy save slot'),
                       source_slot=source_slot,destination_slot=destination_slot)
