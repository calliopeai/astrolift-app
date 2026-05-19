"""Add UserAlertSubscription model (#747)."""

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import core.fields.uuid_v7


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_operations", "0013_event_severity"),
        ("astrolift_registry", "0020_workload_volumes"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserAlertSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("guid", core.fields.uuid_v7.UUIDv7Field(db_index=True, default=core.fields.uuid_v7.uuid7, editable=False, unique=True)),
                (
                    "alert_kind",
                    models.CharField(
                        choices=[
                            ("deploy_success", "Deploy success"),
                            ("deploy_failure", "Deploy failure"),
                            ("error_spike", "Error spike"),
                            ("email_bounce_threshold", "Email bounce threshold"),
                            ("preview_created", "Preview created"),
                            ("preview_destroyed", "Preview destroyed"),
                            ("cert_renewal_failed", "Cert renewal failed"),
                        ],
                        max_length=64,
                    ),
                ),
                (
                    "channel",
                    models.CharField(
                        choices=[
                            ("email", "Email"),
                            ("web", "Web / push"),
                            ("both", "Both"),
                        ],
                        default="both",
                        max_length=16,
                    ),
                ),
                ("enabled", models.BooleanField(default=True)),
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
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "registered_app",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="user_alert_subscriptions",
                        to="astrolift_registry.registeredapp",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="alert_subscriptions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"abstract": False},
        ),
        migrations.AddConstraint(
            model_name="useralertsubscription",
            constraint=models.UniqueConstraint(
                condition=models.Q(deleted_at__isnull=True),
                fields=("user", "registered_app", "alert_kind"),
                name="user_alert_sub_user_app_kind_live_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="useralertsubscription",
            index=models.Index(
                fields=["user", "registered_app"],
                name="user_alert_sub_user_app_idx",
            ),
        ),
    ]
