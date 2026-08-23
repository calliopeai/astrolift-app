"""
Org secret read/write/delete - the module three call sites already import.

``pipeline_secrets.set_pipeline_secret`` / ``delete_pipeline_secret`` and
``secret_plumbing._read_org_secret`` have imported this since #100. It did not
exist, and every import sits inside an exception handler, so nothing surfaced:
writes raised a generic RuntimeError and every read returned None, which made
``resolve_pipeline_secrets`` report every declared secret as missing.

Values live in the control plane (``OrgSecret``), encrypted with the
``core.secrets`` envelope. See that model's docstring for why the control plane
rather than a per-cluster store - briefly, ``runs_on`` can route a job to a
cluster other than the one a write went to, and ``runner_only`` jobs never
touch a cluster at all. Materialisation into a per-job Kubernetes Secret
happens at spawn via ``secret_plumbing.materialize_job_secrets``.

Callers get plaintext ``str``. Treat every return value as a secret bundle:
never log it, never hand it to Temporal, never persist it.
"""

from __future__ import annotations

import logging

from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest

logger = logging.getLogger(__name__)

__all__ = ["read_org_secret", "write_org_secret", "delete_org_secret"]


def _org_pk(org) -> int:
    return getattr(org, "pk", org)


def read_org_secret(org, key: str) -> str | None:
    """Return the plaintext for ``key``, or None when it is not set.

    None is the "not set" signal the callers expect; it is deliberately not
    distinguished from "set but undecryptable". A key whose ciphertext cannot
    be opened is unusable at spawn either way, and reporting the difference
    would tell a caller a secret exists without letting them read it.
    """
    from astrolift_lifecycle.models import OrgSecret

    row = OrgSecret.objects.filter(organization_id=_org_pk(org), key=key).first()
    if row is None:
        return None
    try:
        return decrypt(
            EncryptedSecret(backend_kind=row.backend_kind, backend_ref=bytes(row.ciphertext))
        ).decode("utf-8")
    except Exception:  # noqa: BLE001 - never reflect backend detail to a caller
        logger.warning(
            "org secret could not be decrypted",
            extra={"organization_id": _org_pk(org), "backend_kind": row.backend_kind},
        )
        return None


def write_org_secret(org, key: str, value: str) -> None:
    """Create or replace the value at ``key`` (upsert)."""
    from astrolift_lifecycle.models import OrgSecret

    sealed = encrypt_at_rest(value.encode("utf-8"))
    row = OrgSecret.objects.filter(organization_id=_org_pk(org), key=key).first()
    if row is None:
        OrgSecret.objects.create(
            organization_id=_org_pk(org),
            key=key,
            backend_kind=sealed.backend_kind,
            ciphertext=sealed.backend_ref,
        )
        return
    row.backend_kind = sealed.backend_kind
    row.ciphertext = sealed.backend_ref
    row.save(update_fields=["backend_kind", "ciphertext", "updated_at", "version"])


def list_org_secrets(org, *, prefix: str = "") -> list[str]:
    """Keys the org holds, optionally narrowed to those under ``prefix``.

    ``astrolift_pipelines.pipeline_secrets.get_pipeline_secret_names`` has
    imported this since #100 and it was never written, so the name list a
    pipeline shows for its secrets has always been empty (#1614).

    Names only. A caller that wanted values would call ``read_org_secret``
    per key, which is one decrypt per secret and therefore one place to
    audit rather than a bulk read that hands out everything at once.
    """
    from astrolift_lifecycle.models import OrgSecret

    rows = OrgSecret.objects.filter(organization_id=_org_pk(org))
    if prefix:
        rows = rows.filter(key__startswith=prefix)
    return sorted(rows.values_list("key", flat=True))


def delete_org_secret(org, key: str) -> None:
    """Soft-delete the secret at ``key``. Absent keys are a no-op.

    Soft delete is the repo's rule for business models, and the row carries no
    plaintext - only backend-tagged ciphertext - so a deleted row is not a
    disclosure. The unique constraint is scoped to live rows, so the same key
    can be written again afterwards.
    """
    from astrolift_lifecycle.models import OrgSecret

    row = OrgSecret.objects.filter(organization_id=_org_pk(org), key=key).first()
    if row is not None:
        row.soft_delete()
