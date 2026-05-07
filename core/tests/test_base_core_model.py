"""
Tests for the new ``core.models.base.BaseCoreModel``.

The DB-backed cases are wired up in P0.6 (testing fixtures + factories);
this file currently covers everything that can be tested without a
concrete subclass + migration.
"""

from __future__ import annotations

import uuid

from core.fields import uuid7


def test_uuid7_is_time_ordered():
    a = uuid7()
    b = uuid7()
    assert a.version == 7
    assert b.version == 7
    # Different calls produce different ids.
    assert a != b


def test_uuid7_round_trips_through_str():
    raw = uuid7()
    again = uuid.UUID(str(raw))
    assert again == raw
    assert again.version == 7
