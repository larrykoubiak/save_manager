"""PSP/Vita VMP signing, preserving the destination's 128-byte header.

PS1 derivation from dots-tb's vita-mcr2vmp and Bucanero's psv_resign.c
(GPL-3.0-or-later); see THIRD_PARTY.md. AES is supplied by PyCryptodome.
"""

import hashlib
import hmac
import struct

from Crypto.Cipher import AES

from .card import SIZE

HEADER_SIZE = 128
SIGNATURE = slice(0x20, 0x34)
KEY = bytes.fromhex('ab5abc9fc1f49de6a051dbaefa518859')
IV = bytes.fromhex('b30ffeedb7dc5eb7133da60d1b6b2cdc')


def header(data):
    if (len(data) != SIZE + HEADER_SIZE or data[:4] != b'\0PMV'
            or struct.unpack_from('<I', data, 4)[0] != HEADER_SIZE):
        raise ValueError('Invalid PSP VMP container size or header.')
    return data[:HEADER_SIZE]


def signature(data):
    header(data)
    seed = data[0x0c:0x20]
    cipher = AES.new(KEY, AES.MODE_ECB)
    first = bytes(a ^ b for a, b in zip(cipher.decrypt(seed[:16]), IV))
    second = bytes(a ^ b for a, b in zip(cipher.encrypt(seed[:16]), seed[16:] + b'\xff' * 12))
    salt = first + second[:4] + bytes(44)
    unsigned = data[:SIGNATURE.start] + bytes(20) + data[SIGNATURE.stop:]
    return hmac.new(salt, unsigned, hashlib.sha1).digest()


def verify(data):
    return hmac.compare_digest(data[SIGNATURE], signature(data))


def wrap(content, original):
    if len(content) != SIZE:
        raise ValueError('Expected a raw 128 KiB PS1 memory card.')
    result = bytearray(header(original) + content)
    result[SIGNATURE] = signature(result)
    result = bytes(result)
    if not verify(result):
        raise ValueError('PSP VMP signature verification failed.')
    return result
