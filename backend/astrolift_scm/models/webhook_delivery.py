"""
WebhookDelivery — append-only record of inbound push webhooks.

The receiver creates one row per HMAC-verified delivery keyed on
``(connection, delivery_id)``. The unique constraint is the replay
defense: if the same delivery_id arrives twice, the INSERT raises
IntegrityError and the receiver returns 202 with ``ignored="duplicate"``
without firing a second deploy.

We retain rows for 30 days; older rows are pruned by a daily cron
(``manage.py prune_webhook_deliveries`` — separate task). Storing
fewer rows shrinks the unique-index scan; storing more would let
us defend against a replay attack at the limit of GitHub's webhook
redelivery window (which is 30 days).

Append-only: the model is intentionally never updated. The
``triggered_deployment`` FK gets set on insert; the row is otherwise
write-once.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class WebhookDelivery(BaseCoreModel):
    connection = models.ForeignKey(
        "astrolift_scm.SourceConnection",
        related_name="webhook_deliveries",
        on_delete=models.CASCADE,
    )
    # The host's delivery identifier. GitHub: X-GitHub-Delivery (UUID).
    # GitLab: X-Gitlab-Event-UUID. Whatever the host gives us, we
    # store. Treated as opaque.
    delivery_id = models.CharField(max_length=128, db_index=True)
    host_event = models.CharField(max_length=64, blank=True, default="")
    repo_full_name = models.CharField(max_length=255, blank=True, default="")
    branch = models.CharField(max_length=255, blank=True, default="")
    head_sha = models.CharField(max_length=128, blank=True, default="")
    triggered_deployment = models.ForeignKey(
        "astrolift_lifecycle.Deployment",
        related_name="from_webhook_delivery",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "delivery_id"],
                name="webhook_delivery_unique_per_connection",
            ),
        ]
        indexes = [
            models.Index(
                fields=["connection", "-created_at"],
                name="wh_delivery_conn_created_idx",
            ),
        ]
