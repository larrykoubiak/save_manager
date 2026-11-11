"""Compatibility entry point for the PS2 save report."""

from .backends.ps2.scanner import (
    UnformattedCard, erased_image, card_time, entries, save_size_blocks,
    updated, read_card, scan, date_text, compare, main,
)

if __name__ == '__main__':
    main()


__all__ = ['UnformattedCard', 'erased_image', 'card_time', 'entries', 'save_size_blocks', 'updated', 'read_card', 'scan', 'date_text', 'compare', 'main']
