"""Pure, metadata-only logical Endpoint unions before original KSA UIDs exist.

These values bind an accepted plan; they do not establish current authority,
native ownership, durable submission or workload readiness.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import NoReturn
from uuid import UUID

_DOMAIN = "astrolift.gcp.preparation.union-template.v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_PROJECT = r"[a-z][a-z0-9-]{4,28}[a-z0-9]"
_ROLE = re.compile(rf"projects/({_PROJECT})/roles/[A-Za-z0-9_.]{{1,64}}\Z")
_RESOURCE = re.compile(
    r"projects/[1-9][0-9]{0,19}/locations/[a-z][a-z0-9-]{0,62}/endpoints/[A-Za-z0-9_-]{1,128}\Z"
)
_DNS = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def _refuse() -> NoReturn:
    raise ValueError("INVALID_ACCEPTED_PREPARATION_TEMPLATE")


@dataclass(frozen=True, slots=True)
class EndpointGrant:
    role: str
    resource: str

    def __post_init__(self) -> None:
        if type(self.role) is not str or type(self.resource) is not str:
            _refuse()
        if not _ROLE.fullmatch(self.role) or not _RESOURCE.fullmatch(self.resource):
            _refuse()


@dataclass(frozen=True, slots=True)
class LogicalSubject:
    environment_guid: str
    namespace: str
    name: str

    def __post_init__(self) -> None:
        try:
            guid = UUID(self.environment_guid)
        except (ValueError, AttributeError, TypeError):
            _refuse()
        if str(guid) != self.environment_guid or guid.int == 0:
            _refuse()
        if any(type(value) is not str or not _DNS.fullmatch(value) for value in (self.namespace, self.name)):
            _refuse()


@dataclass(frozen=True, slots=True)
class AcceptedPreparationTemplate:
    permissions: tuple[EndpointGrant, ...]
    subjects: tuple[LogicalSubject, ...]
    source_snapshot_sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.permissions) is not tuple
            or type(self.subjects) is not tuple
            or len(self.permissions) > 64
            or not 1 <= len(self.subjects) <= 64
            or any(type(value) is not EndpointGrant for value in self.permissions)
            or any(type(value) is not LogicalSubject for value in self.subjects)
            or type(self.source_snapshot_sha256) is not str
            or not _SHA.fullmatch(self.source_snapshot_sha256)
        ):
            _refuse()
        if (
            len(set(self.permissions)) != len(self.permissions)
            or len({value.environment_guid for value in self.subjects}) != len(self.subjects)
            or len({(value.namespace, value.name) for value in self.subjects}) != len(self.subjects)
        ):
            _refuse()

    @property
    def payload(self) -> dict:
        return {
            "schema_version": 1,
            "domain": _DOMAIN,
            "permissions": [
                {"role": value.role, "resource": value.resource}
                for value in sorted(self.permissions, key=lambda value: (value.resource, value.role))
            ],
            "subjects": [
                {"environment_guid": value.environment_guid, "namespace": value.namespace, "name": value.name}
                for value in sorted(self.subjects, key=lambda value: value.environment_guid)
            ],
            "source_snapshot_sha256": self.source_snapshot_sha256,
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(
            json.dumps(self.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        ).hexdigest()


def accepted_preparation_template_from_payload(payload: object) -> AcceptedPreparationTemplate:
    """Reject opaque hashes, extra fields, coercions and noncanonical JSON rows."""
    try:
        if type(payload) is not dict or set(payload) != {
            "schema_version",
            "domain",
            "permissions",
            "subjects",
            "source_snapshot_sha256",
        }:
            _refuse()
        if (
            type(payload["schema_version"]) is not int
            or payload["schema_version"] != 1
            or payload["domain"] != _DOMAIN
        ):
            _refuse()
        if type(payload["permissions"]) is not list or type(payload["subjects"]) is not list:
            _refuse()
        if len(payload["permissions"]) > 64 or len(payload["subjects"]) > 64:
            _refuse()
        for row in payload["permissions"]:
            if type(row) is not dict or set(row) != {"role", "resource"}:
                _refuse()
        for row in payload["subjects"]:
            if type(row) is not dict or set(row) != {"environment_guid", "namespace", "name"}:
                _refuse()
        result = AcceptedPreparationTemplate(
            tuple(EndpointGrant(**row) for row in payload["permissions"]),
            tuple(LogicalSubject(**row) for row in payload["subjects"]),
            payload["source_snapshot_sha256"],
        )
        if result.payload != payload:
            _refuse()
        return result
    except (TypeError, KeyError, AttributeError):
        _refuse()
