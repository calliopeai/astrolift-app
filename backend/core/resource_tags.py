"""Operator-defined resource tags, and the rule all three clouds accept (#1505).

`ProvisionSpec.tags` is the operator's channel onto a provisioned resource,
separate from the platform envelope (org / app / env / cluster / isolation)
that each cloud's own builder stamps. Twenty-one drivers read it: AWS
namespaces it under ``astrolift.io/extra/``, Azure passes it as
``custom_tags``, GCP merges it into the resource's labels.

The three do not agree about what a tag may be, so a key that provisions on
one can refuse on another:

* **GCP labels** are the tightest and are what this module conforms to. Keys
  must start with a lowercase letter and hold only lowercase letters, digits,
  ``-`` and ``_``, to 63 characters. Values take the same alphabet and may be
  empty.
* **Azure** allows far more but rejects ``<>%&\\?/`` and caps the value at 256.
* **AWS** is the most permissive of the three.

Conforming to the tightest is what makes a tag portable, which is the whole
point of the field. The alternative — sanitising per cloud — would mean the
string in an operator's billing export is not the string they typed, and they
would have no way to know which one to search for.

So this rejects rather than rewrites, and it rejects at the point the value is
written rather than at provision time. A tag that fails here fails immediately,
in front of the person who typed it; a tag that failed at provision time would
surface as a provision error on one cloud, days later, with the resource half
made.
"""

from __future__ import annotations

import re

# Lowercase-first, then the GCP label alphabet. Anchored, so a key is legal in
# full or not at all.
_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_VALUE = re.compile(r"^[a-z0-9_-]{0,63}$")

# GCP caps labels per resource at 64. The platform envelope already spends
# some of that budget, so the operator's share is bounded well below it.
MAX_TAGS = 24


class ResourceTagError(ValueError):
    """A tag that would not survive all three clouds. Carries the offending key."""

    def __init__(self, message: str, *, key: str = "") -> None:
        super().__init__(message)
        self.key = key


def validate_resource_tags(tags: object) -> dict[str, str]:
    """Return *tags* unchanged, or raise :class:`ResourceTagError`.

    Returns the mapping rather than being a bare assertion so a caller cannot
    accidentally store the unvalidated original.
    """
    if not isinstance(tags, dict):
        raise ResourceTagError("resource tags must be an object of key/value pairs")

    if len(tags) > MAX_TAGS:
        raise ResourceTagError(
            f"at most {MAX_TAGS} resource tags (got {len(tags)}); "
            "GCP caps labels per resource and the platform envelope uses part of that budget"
        )

    for key, value in tags.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ResourceTagError(
                "resource tag keys and values must both be strings",
                key=str(key),
            )
        if not _KEY.match(key):
            raise ResourceTagError(
                f"tag key {key!r} is not portable: it must start with a lowercase letter and "
                "hold only lowercase letters, digits, '-' and '_', to 63 characters. "
                "That is GCP's label rule, which is the tightest of the three clouds",
                key=key,
            )
        if not _VALUE.match(value):
            raise ResourceTagError(
                f"tag value for {key!r} is not portable: it must hold only lowercase letters, "
                "digits, '-' and '_', to 63 characters. That is GCP's label rule, which is the "
                "tightest of the three clouds",
                key=key,
            )
    return tags
