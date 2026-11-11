"""Device connections and read-only FTP acquisition, separate from card formats."""

from contextlib import contextmanager
from ftplib import FTP, error_perm, all_errors
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import tempfile

from save_core.backends import get_backend
from save_core.storage_paths import location_path


MAX_FILE_SIZE = 128 * 1024 * 1024
MAX_SCAN_SIZE = 512 * 1024 * 1024
MAX_ENTRIES = 4096


def clean_text(value, label, limit=4096, empty=False):
    if not isinstance(value, str) or len(value)>limit or any(c in value for c in '\r\n\0') or (not empty and not value.strip()):
        raise ValueError(f'Invalid {label}.')
    return value


def remote_path(root, relative='.'):
    clean_text(root, 'FTP root')
    clean_text(relative, 'relative FTP folder')
    base, child = PurePosixPath(root), PurePosixPath(relative)
    if '\\' in root or '\\' in relative or not base.is_absolute() or child.is_absolute() or '..' in base.parts or '..' in child.parts:
        raise ValueError('Use an absolute FTP root and a relative location without .. components.')
    return base / child


def connection_values(body, old=None):
    old = old or {}
    transport = body.get('transport', old.get('transport','filesystem'))
    if transport not in ('filesystem','ftp'):
        raise ValueError('Choose a mounted folder or FTP connection.')
    root = clean_text(body.get('root_path', old.get('root_path','/')), 'device root folder').strip()
    if transport=='ftp':
        root = str(remote_path(root))
    else:
        path = Path(root).expanduser()
        if not path.is_absolute():
            raise ValueError('Device root must be an absolute path visible to the server/container.')
        root = str(path.resolve())
    host = clean_text(body.get('ftp_host',old.get('ftp_host','')), 'FTP host', 253, empty=transport!='ftp').strip()
    if host and (not re.fullmatch(r'[A-Za-z0-9._:-]+',host) or host.startswith('-')):
        raise ValueError('Enter the FTP host without a URL, path or credentials.')
    port = body.get('ftp_port',old.get('ftp_port',21))
    if type(port) is not int or not 1<=port<=65535:
        raise ValueError('FTP port must be between 1 and 65535.')
    username = clean_text(body.get('ftp_username',old.get('ftp_username','anonymous')), 'FTP username', 200).strip()
    password = clean_text(body.get('ftp_password',old.get('ftp_password','')), 'FTP password', 1024, empty=True)
    return dict(transport=transport,root_path=root,ftp_host=host,ftp_port=port,ftp_username=username,ftp_password=password)


def public_device(device):
    result = dict(device)
    result['has_ftp_password'] = bool(result.pop('ftp_password',''))
    return result


def device_location_path(device, location):
    if device.get('transport','filesystem')=='ftp':
        return remote_path(device['root_path'],location['path'])
    return location_path({**location,'root_path':device['root_path']})


def _ftp_call(device, command, *args, **kwargs):
    try:
        return command(*args, **kwargs)
    except error_perm as error:
        code = str(error)[:3]
        if code=='530':
            raise ValueError('FTP login rejected. Check the username and password.') from None
        if code=='550':
            raise FileNotFoundError('FTP folder or file is unavailable.') from None
        raise OSError('FTP server rejected a read command.') from None
    except all_errors:
        # Do not expose server replies that might echo authentication data.
        raise OSError(f"FTP unavailable at {device['ftp_host']}:{device['ftp_port']}. Start FTP on the device and disconnect other clients.") from None


class FTPReader:
    """Translate connection errors at the wire, preserving local I/O failures."""

    def __init__(self, device, ftp):
        self.device, self.ftp = device, ftp

    def cwd(self, path):
        return _ftp_call(self.device, self.ftp.cwd, path)

    def retrlines(self, command, callback):
        return _ftp_call(self.device, self.ftp.retrlines, command, callback)

    def retrbinary(self, command, callback):
        return _ftp_call(self.device, self.ftp.retrbinary, command, callback)


@contextmanager
def ftp_connection(device):
    ftp = FTP(timeout=8)
    try:
        _ftp_call(device, ftp.connect, device['ftp_host'], device['ftp_port'], timeout=5)
        ftp.sock.settimeout(8)
        ftp.timeout = 8
        _ftp_call(device, ftp.login, device['ftp_username'], device['ftp_password'])
        yield FTPReader(device, ftp)
    finally:
        try:
            if ftp.sock is not None:
                ftp.quit()
        except all_errors:
            pass  # Some PSP servers close QUIT without sending a reply.
        finally:
            ftp.close()


def directory_entries(ftp, path):
    ftp.cwd(str(path))
    lines = []
    ftp.retrlines('LIST',lines.append)
    if len(lines)>MAX_ENTRIES:
        raise ValueError('FTP directory has too many entries; choose a narrower location.')
    result = []
    for line in lines:
        if not line or line.startswith('total '):
            continue
        fields = line.split(None,8)
        if len(fields)==9 and fields[0][0] in ('d','-','l'):
            kind, name = fields[0][0],fields[8]
            if kind=='l':
                continue
            try:
                size = int(fields[4])
            except ValueError:
                raise ValueError('Unsupported FTP directory listing.') from None
        else:
            match = re.fullmatch(r'\d\d-\d\d-\d\d\s+\d\d:\d\d[AP]M\s+(<DIR>|\d+)\s+(.+)',line,re.I)
            if not match:
                raise ValueError('Unsupported FTP directory listing.')
            kind = 'd' if match[1].upper()=='<DIR>' else '-'
            size,name = (0 if kind=='d' else int(match[1])),match[2]
        if name in ('.','..'):
            continue
        clean_text(name,'FTP filename',255)
        if '/' in name or '\\' in name or size<0:
            raise ValueError('Invalid FTP directory entry.')
        result.append((name,kind,size))
    if len({name for name,_,_ in result})!=len(result):
        raise ValueError('Duplicate FTP directory entry.')
    return sorted(result)


def walk_cards(ftp, root, suffixes, recursive=True):
    result = []
    pending = [(PurePosixPath('.'),0)]
    examined = 0
    while pending:
        folder, depth = pending.pop()
        if depth>16:
            raise ValueError('FTP folder nesting is too deep; choose a narrower location.')
        entries = directory_entries(ftp,root/folder)
        examined += len(entries)
        if examined>MAX_ENTRIES:
            raise ValueError('FTP location has too many entries; choose a narrower folder.')
        for name,kind,size in entries:
            relative = folder/name
            if kind=='d' and recursive:
                pending.append((relative,depth+1))
            elif kind=='-' and relative.suffix.lower() in suffixes:
                if size>MAX_FILE_SIZE:
                    raise ValueError('FTP card exceeds the supported download size.')
                result.append((relative.as_posix(),size))
    if sum(size for _,size in result)>MAX_SCAN_SIZE:
        raise ValueError('FTP location exceeds the supported scan size.')
    return sorted(result)


def download(ftp, path, expected_size=None):
    data = bytearray()
    def receive(block):
        if len(data)+len(block)>MAX_FILE_SIZE:
            raise ValueError('FTP card exceeds the supported download size.')
        data.extend(block)
    ftp.retrbinary('RETR '+str(path),receive)
    if expected_size is not None and len(data)!=expected_size:
        raise ValueError(f'{path.name}: FTP file size changed during download. Scan again.')
    return bytes(data)


def cache_folder(cache_root, location):
    path = Path(cache_root)/str(location['device_id'])/str(location['id'])
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    return path


def cache_file(folder, filename):
    relative = PurePosixPath(filename)
    clean_text(filename,'FTP card path')
    if relative.is_absolute() or '..' in relative.parts or relative==PurePosixPath('.') or '\\' in filename:
        raise ValueError('Invalid FTP card path.')
    return folder / relative


def ftp_snapshot(device, location, cache_root):
    """Commit a complete validated local snapshot; failed scans keep the old one."""
    backend = get_backend(location['backend'])
    root = device_location_path(device,location)
    suffixes = backend.suffixes if location['suffix']=='*' else (location['suffix'],)
    folder = cache_folder(cache_root,location)
    with tempfile.TemporaryDirectory(prefix='scan-',dir=folder.parent) as temporary:
        stage = Path(temporary)
        with ftp_connection(device) as ftp:
            if location['marker'] and not any(name==location['marker'] and kind=='-' for name,kind,_ in directory_entries(ftp,root)):
                raise FileNotFoundError('FTP availability marker is missing.')
            files = walk_cards(ftp,root,suffixes,recursive=location['backend']=='ps1')
            for filename,size in files:
                path = cache_file(stage,filename)
                path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                path.write_bytes(download(ftp,root/filename,size))
                path.chmod(0o600)
            if files!=walk_cards(ftp,root,suffixes,recursive=location['backend']=='ps1'):
                raise ValueError('FTP location changed during scan. Scan again when it is idle.')
        cards = backend.snapshot_location({**location,'root_path':str(stage),'path':'.'})
        for filename,_ in files:
            target = cache_file(folder,filename)
            target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            os.replace(cache_file(stage,filename),target)
    return cards


def ftp_source_file(device, location, filename, expected_hash, cache_root):
    """Re-fetch the source before preview/apply; never copy a stale offline cache."""
    folder = cache_folder(cache_root,location)
    target = cache_file(folder,filename)
    with ftp_connection(device) as ftp:
        data = download(ftp,device_location_path(device,location)/PurePosixPath(filename))
    if hashlib.sha256(data).hexdigest()!=expected_hash:
        raise ValueError('FTP card changed since its last scan. Scan the device and preview again.')
    if not target.is_file() or target.read_bytes()!=data:
        target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent,delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(data)
            os.replace(temporary,target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return target
