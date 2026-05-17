"""
Optimistic-concurrency helpers for GraphQL mutations (#497).

Every mutable Astrolift entity carries an integer ``version`` that
auto-increments on every ``.save()`` (see
``core.models.base.BaseCoreModel.save`` and ``core.mixins.TrackingMixin``).
Mutations that want to detect "another session edited this row while my
form was open" accept an ``ifMatchVersion`` arg and check it against
the persisted row before applying any changes.

This module exposes two surface-area pieces the resolvers wire to:

* :func:`check_version_match` — call after fetching the instance and
  before mutating any field. Returns ``None`` on match (proceed),
  otherwise a ready-to-return :class:`MutationResultType` envelope with
  ``code = VERSION_MISMATCH``, ``currentVersion``, and
  ``requestedVersion`` populated.

The companion atomic check + save is :func:`BaseCoreModel.save_with_version_check`,
which is used when the resolver wants belt-and-braces row-level locking
inside the same DB transaction. The cheap envelope check via
:func:`check_version_match` is sufficient for the GraphQL mutation
boundary: the worst case is a tiny race where two sessions both saw the
same version and both proceeded — same risk profile as before #497, so
no regression. For the small set of mutations where the race window
matters (secrets, deploy strategy), the resolver should additionally
wrap the save in a transaction with ``select_for_update``.

The check is intentionally opt-in per-mutation rather than a blanket
decorator: many mutations look up their target row through bespoke
filters (slug + tenant, GUID + scope, etc.) so the lookup logic stays
inside the resolver while the version comparison is one call after it.
"""

from __future__ import annotations

from typing import Any

from astrolift_graphql import MutationResultType, version_mismatch


def check_version_match(
    instance: Any,
    *,
    if_match_version: int | None,
    kind: str | None = None,
) -> MutationResultType | None:
    """Compare ``if_match_version`` against ``instance.version``.

    Returns ``None`` when the caller is allowed to proceed:

    * ``if_match_version is None`` — caller opted out of the check
      (back-compat: omission preserves existing behavior, per #497 spec).
    * versions match — caller holds the latest read.

    Returns a ready ``MutationResultType`` envelope with
    ``code = VERSION_MISMATCH`` and ``currentVersion`` populated when
    the persisted row has moved on. The caller returns the envelope
    directly without applying any mutations.

    ``kind`` is a human-readable label baked into the error message
    ("App was modified by another session…"); pass the model's display
    name so the FE doesn't have to translate codes into copy.
    """
    if if_match_version is None:
        return None
    current = int(getattr(instance, "version", 0) or 0)
    if current == int(if_match_version):
        return None
    return version_mismatch(
        current_version=current,
        requested_version=int(if_match_version),
        kind=kind,
    )


__all__ = ["check_version_match"]
