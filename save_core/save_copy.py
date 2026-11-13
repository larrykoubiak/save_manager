"""Shared mutation plans, verified backups and atomic container replacement."""

from dataclasses import dataclass
from datetime import datetime
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import uuid

from .save_merge import digest, stable_read
from .retroarch_cloud import manifest_for, atomic_write


_data_home = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share').expanduser()
if not _data_home.is_absolute():
    _data_home = Path.home() / '.local' / 'share'
DEFAULT_BACKUPS = _data_home / 'ps2-save-manager' / 'backups'


class CopyApplyError(RuntimeError):
    """A failed card replacement with a verified original backup."""

    def __init__(self, message, backup):
        super().__init__(message)
        self.backup = str(backup)


@dataclass(frozen=True)
class CopyPlan:
    source: Path
    destination: Path
    source_hash: str
    destination_hash: str
    source_save: str
    destination_save: str
    action: str
    candidate: bytes
    original: bytes
    warnings: tuple = ()
    source_slot: int = None
    destination_slot: int = None


def image_file(data, name):
    image = io.BytesIO(data)
    image.name = str(name)
    return image


def apply_copy(plan, backup_root=DEFAULT_BACKUPS):
    """Back up locally, then atomically replace the idle destination card."""
    def unchanged():
        for path, expected in ((plan.source, plan.source_hash), (plan.destination, plan.destination_hash)):
            if digest(stable_read(path)) != expected:
                raise ValueError(path.name + ' changed since the preview. Refresh and preview again.')

    unchanged()
    cloud = manifest_for(plan.destination)
    candidate = plan.original if plan.action == 'Already identical' else plan.candidate
    if plan.action == 'Already identical' and (cloud is None or cloud.matches(candidate)):
        return None
    backup = Path(backup_root) / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    backup.mkdir(parents=True, mode=0o700)
    backup_file = backup / plan.destination.name
    with backup_file.open('xb') as handle:
        os.chmod(backup_file, 0o600)
        handle.write(plan.original)
        handle.flush()
        os.fsync(handle.fileno())
    if digest(backup_file.read_bytes()) != plan.destination_hash:
        raise ValueError('Backup verification failed; destination unchanged.')
    report = {'source': str(plan.source), 'destination': str(plan.destination),
              'source_save': plan.source_save, 'destination_save': plan.destination_save,
              'action': plan.action, 'original_sha256': plan.destination_hash,
              'candidate_sha256': digest(candidate), 'warnings': plan.warnings}
    if plan.source_slot is not None:
        report.update(source_slot=plan.source_slot, destination_slot=plan.destination_slot)
    (backup / 'copy.json').write_text(json.dumps(report, indent=2) + '\n')
    temporary = None
    replaced = False
    try:
        if cloud is not None:
            cloud.backup(backup)
            cloud.unchanged()
        # Some WebDAV servers hide dotfiles from directory refreshes. davfs2
        # can then lose a hidden staging file before rename. Keep it visible,
        # with a .tmp suffix so inventory scans don't treat it as a card.
        fd, temporary = tempfile.mkstemp(prefix=plan.destination.name + '-copy-', suffix='.tmp', dir=plan.destination.parent)
        with os.fdopen(fd, 'wb') as handle:
            os.fchmod(handle.fileno(), stat.S_IMODE(plan.destination.stat().st_mode))
            handle.write(candidate)
            handle.flush()
            os.fsync(handle.fileno())
        if digest(Path(temporary).read_bytes()) != digest(candidate):
            raise ValueError('Staged card verification failed; destination unchanged.')
        unchanged()
        # Do not fall back to truncating a live image if a mount refuses rename.
        os.replace(temporary, plan.destination)
        replaced = True
        if digest(stable_read(plan.destination)) != digest(candidate):
            raise ValueError('Destination read-back differs from the candidate.')
        if cloud is not None:
            cloud.publish(candidate)
    except Exception as error:
        if replaced and cloud is not None:
            try:
                if digest(stable_read(plan.destination))==digest(candidate):
                    atomic_write(plan.destination,plan.original)
                    replaced = False
            except Exception:
                pass  # Report the verified original backup if rollback also fails.
        state = 'Destination may have changed' if replaced else 'Destination was not replaced or was restored'
        raise CopyApplyError(f'{error}\n{state}. Original backup: {backup_file}', backup_file) from error
    finally:
        if temporary is not None and Path(temporary).exists():
            Path(temporary).unlink()
    return backup_file


# Existing Python callers keep working; new application code uses SaveBackend.
def tree_snapshot(card, path='/', seen=frozenset()):
    from .backends.ps2.operations import tree_snapshot as snapshot
    return snapshot(card, path, seen)


def simple_save(card, name):
    from .backends.ps2.operations import simple_save as extract
    return extract(card, name)


def plan_copy(source, record, destination, conflict='stop'):
    from .backends import get_backend
    return get_backend().plan_copy(source, record, destination, conflict)


def plan_delete(path, record):
    from .backends import get_backend
    return get_backend().plan_delete(path, record)


def plan_slot_copy(source, record, source_slot, destination, destination_slot, conflict='stop'):
    from .backends import get_backend
    return get_backend().plan_slot_copy(source, record, source_slot, destination, destination_slot, conflict)
