"""Bounded response identities; these are neither ownership nor completion receipts."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from uuid import UUID


def _guid(value: str) -> None:
    try:
        parsed = UUID(value)
        if str(parsed) == value and parsed.int:
            return
    except (ValueError, TypeError, AttributeError):
        pass
    raise ValueError("ACKNOWLEDGEMENT_IDENTITY_INVALID")


def _digest(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("ACKNOWLEDGEMENT_DIGEST_INVALID")


def acknowledgement_sha256(value: "PreparationAcknowledgement | PolicyAcknowledgement") -> str:
    if type(value) not in (PreparationAcknowledgement, PolicyAcknowledgement):
        raise ValueError("ACKNOWLEDGEMENT_TYPE_INVALID")
    domain = b"astrolift.gcp.native-acknowledgement.v1\0"
    return hashlib.sha256(
        domain
        + json.dumps({"kind": type(value).__name__, **asdict(value)}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class PreparationAcknowledgement:
    submission_id: str
    request_sha256: str
    uid: str
    resource_version: str

    def __post_init__(self) -> None:
        _guid(self.submission_id)
        _guid(self.uid)
        _digest(self.request_sha256)
        if not isinstance(self.resource_version, str) or not re.fullmatch(r"[!-~]{1,256}", self.resource_version):
            raise ValueError("ACKNOWLEDGEMENT_VERSION_INVALID")


@dataclass(frozen=True, slots=True)
class PolicyAcknowledgement:
    submission_id: str
    submission_sha256: str
    policy_sha256: str
    etag_sha256: str

    def __post_init__(self) -> None:
        _guid(self.submission_id)
        for value in (self.submission_sha256, self.policy_sha256, self.etag_sha256):
            _digest(value)


@dataclass(frozen=True, slots=True)
class AcknowledgementReceipt:
    acknowledgement_id: str
    acknowledgement_sha256: str

    def __post_init__(self) -> None:
        _guid(self.acknowledgement_id)
        _digest(self.acknowledgement_sha256)
