"""
OrgSecret - the control-plane store behind ``read/write/delete_org_secret``.

Three call sites have imported ``astrolift_lifecycle.services.secrets`` since
pipeline secrets landed (#100). The module never existed. Each import sits
inside an exception handler, so the failure was silent: pipeline secret writes
raised a generic RuntimeError and ``_read_org_secret`` returned None for every
name, which made ``resolve_pipeline_secrets`` report every declared secret as
missing.

Why the control plane rather than a cluster secret store
--------------------------------------------------------

The cluster stores (``astrolift_dispatch.agent_secrets``) are resolved per
TenantCluster, which works for agents because ``resolve_agent_cluster`` picks
one cluster and the pod reads from it. Pipeline jobs do not have that property:

* ``runs_on: cluster:X`` routes a job to a cluster chosen at dispatch time, so
  a write-time cluster and a read-time cluster need not be the same one;
* ``runner_only`` jobs (self-hosted, macOS) never touch a Kubernetes cluster
  at all and therefore have no cluster store to read from.

Holding the value in the control plane and materialising it per job at spawn
(``secret_plumbing.materialize_job_secrets``, already wired into
``pipeline_job_spawn``) is the only model correct for both.

Values are encrypted with ``core.secrets.encrypt_at_rest``, the same envelope
``SourceConnection.webhook_secret_ciphertext`` uses, so the backend-tagged
ciphertext participates in the existing rotation tooling.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class OrgSecret(BaseCoreModel):
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="org_secrets",
        on_delete=models.CASCADE,
    )

    key = models.CharField(
        max_length=512,
        help_text="Namespaced path, e.g. astrolift/pipelines/<guid>/secrets/<name>.",
    )

    # Mirrors the EncryptedSecret shape: backend_kind identifies which backend
    # produced the ciphertext so the rotation command knows how to re-wrap it.
    backend_kind = models.CharField(max_length=64)
    ciphertext = models.BinaryField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "key"],
                condition=models.Q(deleted_at__isnull=True),
                name="org_secret_unique_live_key_per_org",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "key"], name="org_secret_org_key_idx"),
        ]

    def __str__(self) -> str:
        # Never the value, and never enough of the key to be a credential hint.
        return f"OrgSecret(org={self.organization_id}, key={self.key})"
