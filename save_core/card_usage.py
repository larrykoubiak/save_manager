"""Compatibility export for PS2 capacity reporting."""

from .backends.ps2.usage import card_capacity


__all__ = ['card_capacity']
