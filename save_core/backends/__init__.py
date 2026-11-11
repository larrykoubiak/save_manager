"""Storage backend registry; game-slot decoders live separately in game_plugins."""

from .base import BackendCapabilities, SaveBackend, SaveBundle, SaveFile
from .ps2 import PS2Backend
from .ps1 import PS1Backend


_backends: dict[str, SaveBackend] = {'ps2': PS2Backend(), 'ps1': PS1Backend()}


def backend_info():
    return [dict(id=backend.backend_id,name=backend.backend_id.upper(),suffixes=backend.suffixes)
            for backend in _backends.values()]


def get_backend(backend_id='ps2') -> SaveBackend:
    try:
        return _backends[backend_id]
    except KeyError:
        raise ValueError('Unsupported storage backend: ' + str(backend_id)) from None


__all__ = ['BackendCapabilities', 'SaveBackend', 'SaveBundle', 'SaveFile', 'PS1Backend', 'PS2Backend', 'backend_info', 'get_backend']
