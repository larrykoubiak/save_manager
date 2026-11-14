"""Project one PS1 game's saves between cards without changing other games."""

import json
from pathlib import Path

from .backends.ps1 import PS1Backend
from .backends.ps1.card import Card, blank_card
from .save_copy import CopyPlan
from .save_games import game_serial
from .save_merge import digest, stable_read


def game_contents(data, serial):
    card = Card(PS1Backend.card_data(data))
    return {name: (card.payload(entry), card.metadata(entry))
            for name, entry in card.entries.items() if game_serial(name) == serial}


def game_fingerprint(data, serial):
    # Directory allocation, container signatures and other games are irrelevant.
    records = [(name, len(payload), digest(payload))
               for name, (payload, _) in sorted(game_contents(data, serial).items())]
    return digest(json.dumps(records, separators=(',', ':')).encode())


def plan_game_copy(source, destination, serial, *, initialize_portable=False):
    source, destination = Path(source), Path(destination)
    data, original = stable_read(source), stable_read(destination)
    backend = PS1Backend()
    backend.validate(data, source)
    backend.validate(original, destination)
    before = Card(backend.card_data(original))
    # A native card that already contains only this game needs no reformatting.
    initialize_portable = initialize_portable and any(
        game_serial(name) != serial for name in before.entries)
    if not initialize_portable and game_fingerprint(data, serial) == game_fingerprint(original, serial):
        return CopyPlan(source, destination, digest(data), digest(original), serial, serial,
                        'Already identical', original, original)
    incoming = game_contents(data, serial)
    card = Card(blank_card()) if initialize_portable else Card(bytes(before.data))
    for name in list(card.entries):
        if game_serial(name) == serial:
            card.delete(name)
    for name, (payload, metadata) in sorted(incoming.items()):
        card.put(name, payload, metadata)
    candidate = backend.card_image(bytes(card.data), original)
    backend.validate(candidate, destination)
    if game_fingerprint(candidate, serial) != game_fingerprint(data, serial):
        raise ValueError('Per-game copy failed progress verification.')
    after = Card(backend.card_data(candidate))
    if not initialize_portable:
        unrelated = {name for name in before.entries if game_serial(name) != serial}
        if set(after.entries) != unrelated | set(incoming):
            raise ValueError('Per-game copy changed another game’s save list.')
        for name in unrelated:
            if before.signature(name) != after.signature(name):
                raise ValueError('Per-game copy changed another game’s data or metadata.')
        allowed = set()
        for c in (before, after):
            for name, entry in c.entries.items():
                if game_serial(name) == serial:
                    allowed.update(entry.blocks)
                    for block in entry.blocks:
                        allowed.update(c.remapped.get(block*64+s, block*64+s) for s in range(64))
        for sector in range(len(card.data)//128):
            if sector not in allowed and before.frame(sector) != after.frame(sector):
                raise ValueError('Per-game copy changed an unrelated sector.')
    return CopyPlan(source, destination, digest(data), digest(original), serial, serial,
                    'Already identical' if candidate == original else 'Sync game saves', candidate, original)
