"""Opaque metadata identifiers for secret-location reports; never echo raw refs."""

import hashlib
import json


def metadata_digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def refusal_summary(reason: str, ref, *, namespace: str) -> str:
    return f"{reason}; ref_sha256={metadata_digest(ref)}; expected_namespace={namespace}"
