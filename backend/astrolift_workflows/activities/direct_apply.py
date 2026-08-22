"""
Direct-apply policy (#4, spec 07 §4).

Pure-Python helpers for the ``direct_api`` delivery mode:

* **Field manager constant** — every server-side apply tags fields
  with ``fieldManager=astrolift`` so subsequent applies don't
  conflict with other controllers (cert-manager, ESO, mesh
  injectors, …).
* **Manifest set hashing** — SHA-256 of the canonicalised
  rendered manifest set, returned on :class:`ApplyResult` so two
  deploys producing the same manifest set are recognisable in the
  apply log. It is deliberately *not* written to
  ``Deployment.config_snapshot['manifest_hash']``: that key is
  compared against ``RegisteredApp.manifest_hash``, which hashes the
  normalised astrolift.toml (``astrolift_manifest.normalize``), not
  the rendered k8s objects. Storing this hash there would make the
  config-drift banner fire on every app forever.
* **Dry-run wrapper** — ``apply_with_dry_run(driver, cluster,
  namespace, objects)`` runs dry-run first, aborts with a typed
  exception on schema/RBAC violations, then runs the real apply.
  Idempotent re-apply is the explicit non-failure case (zero diff is
  success).

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
# The value is stamped on the wire by the shared k8s client
# (providers/_sdk/k8s_dynamic_client.py, which hardcodes it because the
# providers tree can't import backend apps); the two are kept in step by
# test_direct_apply.test_driver_stamps_this_field_manager.
FIELD_MANAGER = "astrolift"


# ---- snapshot hashing ----------------------------------------------


def canonical_object_bytes(obj: Mapping[str, object]) -> bytes:
    """Sorted-keys, no-whitespace JSON of one manifest."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


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
    """Outcome of the real apply that followed a clean dry-run."""

    objects_applied: int
    snapshot_hash: str
    diff_was_empty: bool
    """True when re-applying produced zero changes — idempotent
    re-apply path. The deploy workflow records this so the UI can
    show 'no changes' instead of 'redeployed'."""

    created: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    """Per-object failures from the *real* apply, in the driver's
    ``Kind/name: exc`` form. The dry-run gate can't catch everything
    (a conflict raced in between, a quota consumed since), so the
    caller still has to check this."""


def apply_with_dry_run(
    driver,
    cluster,
    namespace: str,
    objects: Iterable[Mapping[str, object]],
) -> ApplyResult:
    """Dry-run the manifest set, then apply it for real.

    ``driver`` is a ``ClusterDriver`` (providers ``_sdk.cluster``):
    every implementation takes ``apply_manifests(cluster, namespace,
    manifests, dry_run=...)`` and returns an ``ApplyResult`` that
    *collects* per-object errors instead of raising. That collecting
    loop is exactly why the gate matters — a set with one schema- or
    RBAC-rejected object still applies every other object before the
    caller sees the failure, leaving the namespace half-updated. The
    dry-run pass moves that verdict ahead of the first mutation.

    Raises :class:`DryRunFailed` when the dry-run reports any error;
    nothing has been mutated at that point.
    """
    objs = list(objects)
    snapshot = manifest_set_sha256(objs)

    dry = driver.apply_manifests(cluster, namespace, objs, dry_run=True)
    if not dry.ok:
        raise DryRunFailed(errors=list(dry.summary()))

    applied = driver.apply_manifests(cluster, namespace, objs, dry_run=False)
    created = tuple(applied.created)
    updated = tuple(applied.updated)
    return ApplyResult(
        objects_applied=len(objs),
        snapshot_hash=snapshot,
        # Nothing created and nothing updated is the idempotent
        # re-apply case: every object was already at the desired state.
        diff_was_empty=not created and not updated,
        created=created,
        updated=updated,
        unchanged=tuple(applied.unchanged),
        errors=tuple(applied.summary()),
    )
