"""
SecretBundle — a named bag of secrets in the platform secrets backend.

Bundles live at the team level; an app references zero or more bundles
per environment via ``AppSecretBundleRef`` with an optional key prefix.
The actual values live in the secrets backend (Vault / SecretsManager
/ GSM / KeyVault) — the model only stores the reference path.
"""

from __future__ import annotations

from django.contrib.postgres.fields import ArrayField
from django.db import models

from core.models.base import BaseCoreModel, NamedBaseCoreModel


class SecretBundle(NamedBaseCoreModel):
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="secret_bundles",
        on_delete=models.CASCADE,
    )
    team = models.ForeignKey(
        "astrolift_identity.Team",
        related_name="secret_bundles",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    backend_ref = models.CharField(max_length=512)

    # ---- Bundle key reflector (#441) ------------------------------
    # Bundle values live in the platform secrets backend (Vault /
    # SecretsManager / GSM / KeyVault); the platform never persists
    # those values.  We do persist the *key names* so the operator UI
    # can answer "how many keys does this bundle project?" without an
    # on-read round-trip to the secrets backend per attachment.
    #
    # Refreshed by:
    # - ``refresh_bundle_known_keys`` activity (fires on every
    #   ``RotateSecretBundleWorkflow`` + ``SecretBundleScheduledRefreshWorkflow``
    #   pass; same activity boundary as the materialise step)
    # - lazy on-read when ``last_key_enum_at`` is older than 1 h
    #   (resolver-side; see ``astrolift_services.bundle_keys``)
    # - explicit ``setBundleSecret`` writes (when that mutation lands)
    last_known_keys = ArrayField(
        base_field=models.CharField(max_length=255),
        default=list,
        blank=True,
    )
    last_key_enum_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["team", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="secret_bundle_slug_unique_active_per_team",
            ),
        ]

    def soft_delete(self, *, by=None) -> None:
        """Override to fan out cleanup of the materialized k8s Secret
        on every cluster this bundle was applied to (#365).

        Soft-delete on the bundle doesn't cascade to
        ``AppSecretBundleRef`` rows (FK is PROTECT for hard-delete;
        soft-delete bypasses on_delete entirely), so the workflow's
        target-list activity still finds every active ref and can
        emit a delete for each materialized k8s Secret.

        Best-effort enqueue: any failure to start the workflow is
        logged but doesn't block the delete. Operators can re-fire
        rotate/delete via the GraphQL mutation if cleanup is
        incomplete — better than blocking the soft-delete on a
        flaky Temporal connection.
        """
        import logging

        target_bundle_pk = self.pk
        super().soft_delete(by=by)
        try:
            from astrolift_workflows.client import start_workflow
            from astrolift_workflows.inputs import (
                Actor,
                DeleteSecretBundleFromClustersInput,
            )

            start_workflow(
                "DeleteSecretBundleFromClustersWorkflow",
                args=[
                    DeleteSecretBundleFromClustersInput(
                        secret_bundle_id=target_bundle_pk,
                        actor=Actor(
                            kind="system",
                            user_id=getattr(by, "pk", None) if by else None,
                            display="bundle-soft-delete-hook",
                        ),
                    ),
                ],
                workflow_id=(f"DeleteSecretBundleFromClustersWorkflow-" f"{self.guid}"),
            )
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "DeleteSecretBundleFromClustersWorkflow enqueue failed "
                "for bundle %s — Secret will linger in cluster(s) until "
                "the next scheduled refresh skips this deleted bundle",
                self.guid,
                exc_info=True,
            )


class AppSecretBundleRef(BaseCoreModel):
    registered_app = models.ForeignKey(
        "astrolift_registry.RegisteredApp",
        related_name="secret_bundle_refs",
        on_delete=models.CASCADE,
    )
    app_environment = models.ForeignKey(
        "astrolift_lifecycle.AppEnvironment",
        related_name="secret_bundle_refs",
        on_delete=models.CASCADE,
    )
    secret_bundle = models.ForeignKey(
        "astrolift_services.SecretBundle",
        related_name="app_refs",
        on_delete=models.PROTECT,
    )
    prefix = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["registered_app", "app_environment", "secret_bundle"],
                condition=models.Q(deleted_at__isnull=True),
                name="appsecret_ref_unique_active",
            ),
        ]
