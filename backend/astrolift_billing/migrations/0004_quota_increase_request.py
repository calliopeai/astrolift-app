"""QuotaIncreaseRequest model — operator request to bump a quota (#434).

See ``astrolift_billing.models.quota_increase_request`` for the model
docstring. The frontend surfaces a "Request bump" button when
``current_usage / soft_limit > 0.8``; the mutation creates a row +
notifies org admins.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import core.fields.uuid_v7


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_billing", "0003_costsnapshot_managed_service_binding"),
        ("astrolift_identity", "0008_resync_system_roles_cluster_manage"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="QuotaIncreaseRequest",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("version", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                (
                    "guid",
                    core.fields.uuid_v7.UUIDv7Field(
                        db_index=True, default=core.fields.uuid_v7.uuid7, editable=False, unique=True
                    ),
                ),
                (
                    "requested_factor",
                    models.DecimalField(
                        decimal_places=2,
                        help_text="Multiplier requested against the current hard limit (e.g. 2.0 = double).",
                        max_digits=6,
                    ),
                ),
                (
                    "reason",
                    models.TextField(help_text="Operator's justification. Required at the mutation entry."),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")],
                        db_index=True,
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                (
                    "decision_note",
                    models.TextField(
                        blank=True,
                        default="",
                        help_text="Approver's note. Surfaces under the row in the requester's UI when present.",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Creator",
                    ),
                ),
                (
                    "decided_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="quota_requests_decided",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "deleted_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="quota_increase_requests",
                        to="astrolift_identity.organization",
                    ),
                ),
                (
                    "quota",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="increase_requests",
                        to="astrolift_billing.quota",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="quota_requests_made",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["organization", "status", "-created_at"], name="quota_req_org_status_idx"
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("deleted_at__isnull", True), ("status", "pending")),
                        fields=("quota",),
                        name="quota_request_one_pending_per_quota",
                    )
                ],
            },
        ),
    ]
