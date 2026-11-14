"""Proving Ground US named careers share the validated Tony Hawk save envelope."""

from .thps4 import THPS4CareerUS


class THPGCareerUS(THPS4CareerUS):
    name = "Tony Hawk's Proving Ground career (US PS2)"
    prefix = 'BASLUS-21616'
    payload_size = 169984
    save_version = 76
    icon_filename = 'thpg.ico'
    icon_title = 'THPG:'
