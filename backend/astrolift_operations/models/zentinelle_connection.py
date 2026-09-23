"""An organization's connection to a Zentinelle deployment, and the clusters it registered (#1887).

Zentinelle is the control plane; the gateway it governs agents through runs
inside each cluster as a data plane. An org admin connects with Zentinelle's
URL and a one-time enrollment code, which Zentinelle exchanges once for an
install credential (``sk_astroinst_``). That credential registers clusters,
and each registration answers with the cluster gateway's own credential
(``sk_gateway_``), which goes straight into a Secret in the cluster.

Per organization rather than one row per install: the audit evidence stream
to Zentinelle is already configured per org (``WebhookSubscription``), and
connecting is an org-admin action, so a single install-wide row would let
one org's admin rewire every org's governance.

The install credential is kept encrypted with ``core.secrets``, like SCM
tokens. The gateway credential is not kept at all: Zentinelle returns it
once, it is written into the cluster Secret, and a lost one is replaced by
registering the cluster again, which Zentinelle treats as a rotation.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import BaseCoreModel


class ZentinelleConnection(BaseCoreModel):
    """One organization's link to one Zentinelle deployment."""

    class Status(models.TextChoices):
        CONNECTED = "connected"
        REVOKED = "revoked"
        """Zentinelle refused the install credential: it was disconnected
        on the Zentinelle side. Disconnect here and connect again."""
        DISCONNECTED = "disconnected"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="zentinelle_connections",
        on_delete=models.CASCADE,
    )
    base_url = models.URLField(max_length=500)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.CONNECTED)
    zentinelle_install_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Zentinelle's id for this install (AstroliftInstall).",
    )
    tenant_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="Zentinelle tenants the enrollment code scoped this install to.",
    )
    credential_backend_kind = models.CharField(max_length=32, default="local_fernet")
    credential_ciphertext = models.BinaryField(blank=True, default=b"")
    connected_at = models.DateTimeField(null=True, blank=True)
    connected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    disconnected_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("organization",),
                condition=models.Q(deleted_at__isnull=True),
                name="zentinelle_connection_one_live_per_org",
            )
        ]

    def __str__(self) -> str:
        return f"ZentinelleConnection({self.organization_id}, {self.base_url}, {self.status})"


class ZentinelleClusterGateway(BaseCoreModel):
    """A cluster registered with Zentinelle through a connection, and its gateway."""

    connection = models.ForeignKey(
        ZentinelleConnection,
        related_name="cluster_gateways",
        on_delete=models.CASCADE,
    )
    cluster = models.ForeignKey(
        "astrolift_clusters.TenantCluster",
        related_name="zentinelle_gateways",
        on_delete=models.CASCADE,
    )
    zentinelle_cluster_id = models.CharField(
        max_length=128,
        help_text="The id Zentinelle knows the cluster by: the TenantCluster guid, "
        "also the gateway's ZENTINELLE_CLUSTER_ID.",
    )
    gateway_name = models.CharField(max_length=255, blank=True, default="")
    gateway_enabled = models.BooleanField(
        default=True,
        help_text="Whether this cluster's gateway should run. Deployed only while "
        "ZENTINELLE_GATEWAY_ENABLED is on.",
    )
    gateway_deployed = models.BooleanField(
        default=False,
        help_text="Whether the gateway Deployment and Service were applied and not removed since.",
    )
    registered_at = models.DateTimeField(null=True, blank=True)
    credential_rotated_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(
        blank=True,
        default="",
        help_text="Why the last operation on this cluster failed; empty after one succeeds.",
    )

    class Meta:
        constraints = [
            # One Secret and one Deployment name exist per cluster, so a
            # cluster has at most one gateway whichever org registered it.
            models.UniqueConstraint(
                fields=("cluster",),
                condition=models.Q(deleted_at__isnull=True),
                name="zentinelle_gateway_one_live_per_cluster",
            )
        ]

    @property
    def status(self) -> str:
        """``error`` while the last operation's failure stands, else ``deployed`` or ``registered``."""
        if self.last_error:
            return "error"
        return "deployed" if self.gateway_deployed else "registered"

    def __str__(self) -> str:
        return f"ZentinelleClusterGateway({self.zentinelle_cluster_id}, {self.status})"
