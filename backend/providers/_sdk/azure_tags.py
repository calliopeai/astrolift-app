"""Azure Resource Manager tag serialization.

Azure's tag vocabulary differs from AWS's: tag names cannot contain
``<>%&\\?/``, names are case-insensitive, resources accept at most 50 tags,
and values are limited to 256 characters. Keep these rules in one place so
managed-service ownership and billing tags cannot drift by driver.
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

AZURE_TAG_LIMIT = 50
AZURE_TAG_NAME_LIMIT = 512
AZURE_TAG_VALUE_LIMIT = 256
PLATFORM_TAG_PREFIX = "astrolift.io/"
AZURE_PLATFORM_PREFIX = "astrolift-"

_FORBIDDEN = re.compile(r"[<>%&\\?/]")
_UNSAFE = re.compile(r"[^a-z0-9-]+")
_HYPHENS = re.compile(r"-+")


class AzureTagError(ValueError):
    """An Azure tag set cannot be represented without ambiguity."""


def serialize_azure_arm_tags(
    platform_tags: Mapping[str, object],
    *,
    custom_tags: Mapping[str, object] | None = None,
) -> dict[str, str]:
    """Serialize canonical platform and user tags to valid ARM names.

    Canonical ``astrolift.io/<name>`` keys have a stable, readable mapping to
    ``astrolift-<name>``. User keys are namespaced and include a digest so
    normalization cannot silently collapse distinct keys.
    """

    output: dict[str, str] = {}
    seen: dict[str, str] = {}
    for key, value in platform_tags.items():
        if not str(key).startswith(PLATFORM_TAG_PREFIX):
            raise AzureTagError(f"platform tag {key!r} must use the {PLATFORM_TAG_PREFIX!r} namespace")
        _add_tag(output, seen, _platform_key(str(key)), value, source=str(key))
    for key, value in (custom_tags or {}).items():
        _add_tag(output, seen, _custom_key(str(key)), value, source=str(key))
    if len(output) > AZURE_TAG_LIMIT:
        raise AzureTagError(f"Azure resources support at most {AZURE_TAG_LIMIT} tags (got {len(output)})")
    return output


def _platform_key(key: str) -> str:
    suffix = _slug(key.removeprefix(PLATFORM_TAG_PREFIX))
    if not suffix:
        raise AzureTagError(f"platform tag {key!r} has no usable name")
    return f"{AZURE_PLATFORM_PREFIX}{suffix}"


def _custom_key(key: str) -> str:
    digest = hashlib.sha256(key.encode()).hexdigest()[:8]
    readable = _slug(key)[:96].rstrip("-") or "tag"
    return f"{AZURE_PLATFORM_PREFIX}extra-{readable}-{digest}"


def _add_tag(
    output: dict[str, str],
    seen: dict[str, str],
    key: str,
    value: object,
    *,
    source: str,
) -> None:
    if len(key) > AZURE_TAG_NAME_LIMIT or _FORBIDDEN.search(key):
        raise AzureTagError(f"Azure tag name {key!r} is invalid")
    casefolded = key.casefold()
    previous = seen.get(casefolded)
    if previous is not None and previous != source:
        raise AzureTagError(f"Azure tag keys {previous!r} and {source!r} normalize to the same name")
    serialized = str(value)
    if len(serialized) > AZURE_TAG_VALUE_LIMIT:
        raise AzureTagError(
            f"Azure tag value for {source!r} exceeds {AZURE_TAG_VALUE_LIMIT} characters",
        )
    seen[casefolded] = source
    output[key] = serialized


def _slug(value: str) -> str:
    return _HYPHENS.sub("-", _UNSAFE.sub("-", value.lower())).strip("-")
