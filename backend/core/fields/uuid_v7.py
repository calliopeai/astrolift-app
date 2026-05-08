"""
UUIDv7 field type.

UUIDv7 is the external-facing identifier for every BaseCoreModel. It is
a time-ordered UUID (RFC 9562) so primary-key-ordered scans match
chronological order, which matters for log-style and event-style tables.

Python 3.13 ships ``uuid.uuid7``; on 3.12 we generate v7 inline.
"""

from __future__ import annotations

import os
import time
import uuid

from django.db import models


def uuid7() -> uuid.UUID:
    ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    rand_b = int.from_bytes(os.urandom(8), "big") & 0x3FFFFFFFFFFFFFFF
    int_value = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=int_value)


class UUIDv7Field(models.UUIDField):
    """UUIDField that defaults to a freshly-generated UUID v7."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("default", uuid7)
        kwargs.setdefault("editable", False)
        super().__init__(*args, **kwargs)
