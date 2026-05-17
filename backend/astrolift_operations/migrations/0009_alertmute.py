"""AlertMute model — TTL-based silence on an AlertRule (#434).

See ``astrolift_operations.models.alert_mute`` for the model docstring.
The delivery worker consults :func:`astrolift_operations.alert_mute.is_rule_muted`
before fan-out.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import core.fields.uuid_v7


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_identity", "0008_resync_system_roles_cluster_manage"),
        ("astrolift_operations", "0008_audit_export"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AlertMute",
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
                    "ttl_until",
                    models.DateTimeField(
                        help_text="When the mute auto-expires. The delivery worker treats the rule as live again after this instant."
                    ),
                ),
                (
                    "reason",
                    models.TextField(
                        blank=True,
                        default="",
                        help_text="Why the rule was muted. Required by ops convention even though the field is blank-tolerant — the mutation enforces presence.",
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
                    "muted_by",
                    models.ForeignKey(
                        blank=True,
                        help_text="User who issued the mute. Distinct from BaseCoreModel.created_by because that field tracks the row writer; this one is semantically the mute owner.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="alert_mutes_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="alert_mutes",
                        to="astrolift_identity.organization",
                    ),
                ),
                (
                    "rule",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mutes",
                        to="astrolift_operations.alertrule",
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
                    models.Index(fields=["rule", "ttl_until"], name="alert_mute_rule_ttl_idx"),
                    models.Index(fields=["organization", "ttl_until"], name="alert_mute_org_ttl_idx"),
                ],
            },
        ),
    ]
