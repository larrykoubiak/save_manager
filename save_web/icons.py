"""Cached icon rendering dispatched by the backend asset encoding."""

import logging
import re
import threading

from save_core.backends.ps2.icons import encode_gif, loop_seconds, pose_at, validate_model
from save_core.backends.ps1.icons import encode_gif as encode_ps1_gif

CACHE_VERSION = 'v4'


class IconCache:
    def __init__(self, service):
        self.db = service.db
        self.directory = service.data_dir / 'icons'
        self.directory.mkdir(exist_ok=True)
        self.lock = threading.Lock()

    def get(self, key, animated=True):
        if key != 'placeholder' and not re.fullmatch('[0-9a-f]{64}', key):
            raise LookupError('Icon not found.')
        path = self.directory / f'{CACHE_VERSION}-{key}-{"animated" if animated else "still"}.gif'
        with self.lock:
            if path.is_file():
                return path.read_bytes()
            model = None if key == 'placeholder' else self.db.one('SELECT model FROM icons WHERE sha256=?', (key,))['model']
            try:
                data = encode_ps1_gif(model[4:], animated) if model and model.startswith(b'PS1I') else encode_gif(model, animated)
            except Exception as error:
                logging.getLogger(__name__).warning('Cannot render save icon %s; using placeholder: %s', key, error)
                data = encode_gif()
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(data)
            temporary.replace(path)
            return data


__all__ = ['IconCache', 'loop_seconds', 'pose_at', 'validate_model']
