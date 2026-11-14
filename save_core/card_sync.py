"""Whole-card replacement plans, with encoding owned by each storage backend."""

from pathlib import Path

from .backends import get_backend
from .save_copy import CopyPlan
from .save_merge import digest, stable_read


def plan_card_copy(backend_id, source, destination):
    source, destination = Path(source), Path(destination)
    backend = get_backend(backend_id)
    data, original = stable_read(source), stable_read(destination)
    backend.validate(data, source)
    content = backend.card_data(data)
    candidate = backend.card_image(content, original)
    backend.validate(candidate, destination)
    if backend.card_data(candidate) != content:
        raise ValueError('Whole-card conversion failed verification.')
    return CopyPlan(source, destination, digest(data), digest(original),
                    'Whole memory card', 'Whole memory card',
                    'Already identical' if candidate == original else 'Replace memory card',
                    candidate, original)
