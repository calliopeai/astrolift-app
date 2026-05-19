import core.fields.uuid_v7
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0017_merge_0016_leaves"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="RetentionPolicy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("guid", core.fields.uuid_v7.UUIDv7Field(db_index=True, default=core.fields.uuid_v7.uuid7, editable=False, unique=True)),
                (
                    "signal",
                    models.CharField(
                        choices=[
                            ("logs", "Logs"),
                            ("metrics", "Metrics"),
                            ("traces", "Traces"),
                            ("audit_events", "Audit Events"),
                        ],
                        max_length=32,
                    ),
                ),
                ("retention_days", models.PositiveIntegerField()),
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
                        related_name="retention_policies",
                        to="astrolift_registry.registeredapp",
                    ),
                ),
            ],
            options={"abstract": False},
        ),
        migrations.AddIndex(
            model_name="retentionpolicy",
            index=models.Index(
                fields=["registered_app", "signal"],
                name="retention_policy_app_signal_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="retentionpolicy",
            constraint=models.UniqueConstraint(
                condition=models.Q(deleted_at__isnull=True),
                fields=("registered_app", "signal"),
                name="retention_policy_unique_active_app_signal",
            ),
        ),
    ]
