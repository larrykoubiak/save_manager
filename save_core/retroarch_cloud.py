"""Publish mounted RetroArch cloud-card edits in its MD5 server manifest."""

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


def atomic_write(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=path.name+'-',suffix='.tmp',dir=path.parent,delete=False) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(),path.stat().st_mode & 0o777)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if temporary.read_bytes()!=data:
            raise OSError('Staged cloud metadata failed verification.')
        os.replace(temporary,path)
        if path.read_bytes()!=data:
            raise OSError('Cloud metadata read-back differs from the update.')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class RetroArchManifest:
    def __init__(self, path, key):
        self.path, self.key = path, key
        self.original = path.read_bytes()
        try:
            self.rows = json.loads(self.original)
            if not isinstance(self.rows,list):
                raise ValueError()
            names = set()
            for row in self.rows:
                if not isinstance(row,dict) or not isinstance(row.get('path'),str) or row['path'].encode('utf-8').lower() in names:
                    raise ValueError()
                names.add(row['path'].encode('utf-8').lower())
                if row.get('hash') not in (None, '') and not re.fullmatch('[0-9a-fA-F]{32}',row['hash']):
                    raise ValueError()
        except (ValueError,TypeError):
            raise ValueError('Invalid RetroArch cloud manifest. Card was not changed.') from None

    def matches(self, data):
        expected = hashlib.md5(data).hexdigest()
        return any(row['path']==self.key and row.get('hash')==expected for row in self.rows)

    def unchanged(self):
        if self.path.read_bytes()!=self.original:
            raise ValueError('RetroArch cloud manifest changed during the transfer. Retry after cloud sync finishes.')

    def backup(self, directory):
        path = directory/'retroarch-manifest.server.json'
        with path.open('xb') as handle:
            os.chmod(path,0o600)
            handle.write(self.original)
            handle.flush()
            os.fsync(handle.fileno())
        if path.read_bytes()!=self.original:
            raise OSError('RetroArch manifest backup failed verification.')

    def publish(self, data):
        self.unchanged()
        expected = hashlib.md5(data).hexdigest()
        rows = [dict(row) for row in self.rows]
        row = next((row for row in rows if row['path']==self.key),None)
        if row is None:
            rows.append(dict(path=self.key,hash=expected))
        else:
            row['hash'] = expected
        candidate = (json.dumps(sorted(rows,key=lambda row:row['path'].encode('utf-8').lower()),indent=2)+'\n').encode()
        try:
            atomic_write(self.path,candidate)
        except Exception:
            # Do not overwrite another client's manifest when attempting rollback.
            if self.path.read_bytes()==candidate:
                atomic_write(self.path,self.original)
            raise


def manifest_for(card):
    card = Path(card)
    for parent in card.parents:
        relative = card.relative_to(parent)
        if relative.parts[0]!='saves':
            continue
        path = parent/'manifest.server'
        if path.is_file():
            return RetroArchManifest(path,relative.as_posix())
    return None
