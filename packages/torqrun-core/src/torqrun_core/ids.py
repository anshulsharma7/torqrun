"""Time-ordered UUIDs (RFC 9562 version 7).

Python 3.12's ``uuid`` module has no ``uuid7``. Time-ordered keys keep B-tree inserts at the
right edge of the index and make IDs roughly sortable by creation time.
"""

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")  # 80 random bits; 74 are used
    value = (ms & 0xFFFF_FFFF_FFFF) << 80
    value |= 0x7 << 76  # version
    value |= ((rand >> 62) & 0xFFF) << 64  # rand_a: 12 bits
    value |= 0b10 << 62  # RFC 4122 variant
    value |= rand & ((1 << 62) - 1)  # rand_b: 62 bits
    return uuid.UUID(int=value)
