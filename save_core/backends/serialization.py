"""Lossless, explicit JSON encoding of backend metadata; never pickle data."""

import base64

from .base import SaveBundle, SaveFile
from .ps1.card import Metadata as PS1Metadata


def encode(value):
    if isinstance(value, bytes):
        return {'bytes': base64.b64encode(value).decode('ascii')}
    if isinstance(value, tuple):
        return {'tuple': [encode(item) for item in value]}
    if isinstance(value, PS1Metadata):
        return {'ps1': [value.size, encode(value.frames)]}
    if value is None or type(value) in (int, str, bool):
        return value
    raise ValueError('Unsupported backend metadata type: ' + type(value).__name__)


def decode(value):
    if isinstance(value, dict):
        if set(value)=={'bytes'}:
            return base64.b64decode(value['bytes'], validate=True)
        if set(value)=={'tuple'}:
            return tuple(decode(item) for item in value['tuple'])
        if set(value)=={'ps1'}:
            size, frames = value['ps1']
            return PS1Metadata(size, decode(frames))
        raise ValueError('Unsupported backend metadata encoding.')
    if value is None or type(value) in (int, str, bool):
        return value
    raise ValueError('Unsupported backend metadata value.')


def bundle_manifest(bundle, put):
    return dict(save_id=bundle.save_id, metadata=encode(bundle.metadata), files=[
        dict(name=file.name, object=put(file.data), metadata=encode(file.metadata)) for file in bundle.files])


def read_bundle(manifest, get):
    return SaveBundle(manifest['save_id'], tuple(
        SaveFile(file['name'], get(file['object']), decode(file['metadata'])) for file in manifest['files']),
        decode(manifest['metadata']))
