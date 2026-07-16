"""Shared fixtures for GCP driver tests.

GCP doesn't have a moto-equivalent that's broadly usable, so each
driver receives a stub client via the `client=` constructor kwarg.
The stubs implement just enough of the google-cloud-* surface to
exercise the driver's policy logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

# ----- google-cloud exception stand-ins ---------------------------
# These mimic the .__class__.__name__ matching that the drivers use
# for type dispatch via gcp/_errors.py.


class NotFound(Exception):
    pass


class AlreadyExists(Exception):
    pass


class FailedPrecondition(Exception):
    pass


class PermissionDenied(Exception):
    pass


class Conflict(Exception):
    pass


@pytest.fixture
def gcp_exceptions() -> dict[str, type[Exception]]:
    return {
        "NotFound": NotFound,
        "AlreadyExists": AlreadyExists,
        "FailedPrecondition": FailedPrecondition,
        "PermissionDenied": PermissionDenied,
        "Conflict": Conflict,
    }


# ----- generic stub fakes -----------------------------------------


@dataclass
class FakeRecordSet:
    name: str
    record_type: str
    ttl: int = 300
    rrdatas: list[str] = field(default_factory=list)


@dataclass
class FakeManagedZone:
    name: str
    dns_name: str
    record_sets: list[FakeRecordSet] = field(default_factory=list)
    pending_changes: list[Any] = field(default_factory=list)

    def changes(self) -> _FakeChanges:
        return _FakeChanges(zone=self)

    def list_resource_record_sets(self) -> list[FakeRecordSet]:
        return list(self.record_sets)

    def resource_record_set(
        self,
        fqdn: str,
        type: str,
        ttl: int,
        values: list[str],
    ) -> FakeRecordSet:
        return FakeRecordSet(
            name=fqdn,
            record_type=type,
            ttl=ttl,
            rrdatas=list(values),
        )


@dataclass
class _FakeChanges:
    zone: FakeManagedZone
    additions: list[FakeRecordSet] = field(default_factory=list)
    deletions: list[FakeRecordSet] = field(default_factory=list)

    def add_record_set(self, rs: FakeRecordSet) -> None:
        self.additions.append(rs)

    def delete_record_set(self, rs: FakeRecordSet) -> None:
        self.deletions.append(rs)

    def create(self) -> None:
        # Apply deletions then additions to the zone's in-memory list
        kept = [
            rs
            for rs in self.zone.record_sets
            if not any(rs.name == d.name and rs.record_type == d.record_type for d in self.deletions)
        ]
        self.zone.record_sets = kept + self.additions


@dataclass
class FakeDNSClient:
    project: str
    zones: list[FakeManagedZone] = field(default_factory=list)

    def list_zones(self) -> list[FakeManagedZone]:
        return list(self.zones)

    def zone(self, name: str) -> FakeManagedZone:
        for z in self.zones:
            if z.name == name:
                return z
        raise NotFound(f"zone {name}")
