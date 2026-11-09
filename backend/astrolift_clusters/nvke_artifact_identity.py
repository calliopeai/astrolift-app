"""Serialize caller-selected artifact UUID reservations across tables and originals."""

import hashlib
from uuid import UUID

from django.db import connection


def lock(identity):
    if not connection.in_atomic_block or connection.vendor != "postgresql":
        raise RuntimeError("Artifact reservation requires a PostgreSQL transaction")
    value = UUID(str(identity))
    key = int.from_bytes(
        hashlib.sha256(b"nvke.artifact.identity.v1\0" + value.bytes).digest()[:8],
        "big",
        signed=True,
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])
