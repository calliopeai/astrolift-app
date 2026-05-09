"""
Direct-apply policy (#4, spec 07 §4).

Pure-Python helpers for the ``direct_api`` delivery mode:

* **Field manager constant** — every server-side apply tags fields
  with ``fieldManager=astrolift`` so subsequent applies don't
  conflict with other controllers (cert-manager, ESO, mesh
  injectors, …).
* **Manifest set hashing** — SHA-256 of the canonicalised
  rendered manifest set. Stored on
  ``Deployment.config_snapshot`` so two deploys producing the same
  manifest set are recognisable, and rollback can verify
  byte-identity.
* **Dry-run wrapper** — ``apply_with_dry_run(driver, cluster,
  objects)`` runs dry-run first, aborts with a typed exception on
  schema/RBAC violations, then runs the real apply. Idempotent
  re-apply is the explicit non-failure case (zero diff is success).

The actual ``apply_manifests`` k8s call lives on the
``ClusterDriver`` protocol (#11).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Iterable, Mapping


# Spec 07 §4 — fieldManager string. Must be stable across re-applies
# because k8s tracks ownership by this string. Renaming would re-own
# every field every release — silent flapping with other controllers.
FIELD_MANAGER = "astrolift"


# ---- snapshot hashing ----------------------------------------------


def canonical_object_bytes(obj: Mapping[str, object]) -> bytes:
    """Sorted-keys, no-whitespace JSON of one manifest."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def manifest_set_sha256(objects: Iterable[Mapping[str, object]]) -> str:
    """Hash the entire rendered manifest set.

    Order-independent: objects are sorted by (kind, namespace, name)
    before hashing so cosmetic re-orderings don't produce a 'new'
    snapshot. Returns ``sha256:<64-hex>``.
    """
    sortable = []
    for o in objects:
        meta = o.get("metadata", {}) if isinstance(o, dict) else {}
        kind = str(o.get("kind", ""))
        ns = str(meta.get("namespace", "")) if isinstance(meta, dict) else ""
        name = str(meta.get("name", "")) if isinstance(meta, dict) else ""
        sortable.append(((kind, ns, name), o))
    sortable.sort(key=lambda t: t[0])

    h = hashlib.sha256()
    for _, obj in sortable:
        h.update(canonical_object_bytes(obj))
        h.update(b"\n")
    return "sha256:" + h.hexdigest()


# ---- dry-run / apply ------------------------------------------------


class DryRunFailed(Exception):
    """Server-side dry-run failed (schema, RBAC, conflict). Caller
    aborts the deploy without touching the live state."""

    def __init__(self, *, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors) if errors else "dry-run failed")


@dataclasses.dataclass(frozen=True, slots=True)
class ApplyResult:
    """Output of a successful apply."""

    objects_applied: int
    snapshot_hash: str
    diff_was_empty: bool
    """True when re-applying produced zero changes — idempotent
    re-apply path. The deploy workflow records this so the UI can
    show 'no changes' instead of 'redeployed'."""


def apply_with_dry_run(
    driver,
    cluster,
    objects: Iterable[Mapping[str, object]],
    *,
    field_manager: str = FIELD_MANAGER,
) -> ApplyResult:
    """Run dry-run then real apply.

    The driver protocol is structural; tests pass a stub with the
    same shape as ``ClusterDriver.apply``. This wrapper:

      1. Runs ``driver.apply(cluster, objects, dry_run=True,
         field_manager=...)``.
      2. On failure, raises :class:`DryRunFailed` carrying the
         driver's reported errors.
      3. On success, runs the real apply and returns the snapshot
         hash + diff-emptiness signal for the audit log.

    Drivers that don't accept ``dry_run`` / ``field_manager``
    kwargs (older test stubs) fall back to a positional call with
    the kwargs ignored — this lets us land the policy module ahead
    of the driver expansion.
    """
    objs = list(objects)
    snapshot = manifest_set_sha256(objs)

    dry_run_errors = _try_dry_run(
        driver, cluster, objs, field_manager=field_manager,
    )
    if dry_run_errors:
        raise DryRunFailed(errors=dry_run_errors)

    diff_was_empty = _apply(
        driver, cluster, objs, field_manager=field_manager,
    )

    return ApplyResult(
        objects_applied=len(objs),
        snapshot_hash=snapshot,
        diff_was_empty=bool(diff_was_empty),
    )


def _try_dry_run(driver, cluster, objs, *, field_manager: str) -> list[str]:
    """Driver call with kwargs that older stubs may not have. We
    catch TypeError once for the kwargs fallback, then propagate
    anything else."""
    try:
        result = driver.apply(
            cluster, objs, dry_run=True, field_manager=field_manager,
        )
    except TypeError:
        # Older driver shape — call without kwargs.
        result = driver.apply(cluster, objs)
    if isinstance(result, dict) and result.get("dry_run_errors"):
        return list(result["dry_run_errors"])
    return []


def _apply(driver, cluster, objs, *, field_manager: str) -> bool:
    try:
        result = driver.apply(
            cluster, objs, dry_run=False, field_manager=field_manager,
        )
    except TypeError:
        result = driver.apply(cluster, objs)
    if isinstance(result, dict):
        return bool(result.get("diff_was_empty", False))
    return False
