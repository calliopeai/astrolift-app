import core.fields.uuid_v7
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("astrolift_identity", "__first__"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # --- JobRun first (no Runner FK yet) ---
        migrations.CreateModel(
            name="JobRun",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("version", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "deleted_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                (
                    "guid",
                    core.fields.uuid_v7.UUIDv7Field(
                        db_index=True,
                        default=core.fields.uuid_v7.uuid7,
                        editable=False,
                        unique=True,
                    ),
                ),
                ("job_name", models.CharField(db_index=True, max_length=200)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("queued", "Queued"),
                            ("running", "Running"),
                            ("success", "Success"),
                            ("failure", "Failure"),
                            ("cancelled", "Cancelled"),
                            ("skipped", "Skipped"),
                            ("timed_out", "Timed Out"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("steps_payload", models.JSONField(blank=True, default=list)),
                ("steps_result", models.JSONField(blank=True, default=list)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="job_runs",
                        to="astrolift_identity.organization",
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
                    "deleted_by",
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
                "abstract": False,
            },
        ),
        # --- Runner (current_job_run FK → JobRun which now exists) ---
        migrations.CreateModel(
            name="Runner",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("version", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "deleted_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                (
                    "guid",
                    core.fields.uuid_v7.UUIDv7Field(
                        db_index=True,
                        default=core.fields.uuid_v7.uuid7,
                        editable=False,
                        unique=True,
                    ),
                ),
                ("name", models.CharField(max_length=200)),
                ("slug", models.SlugField(db_index=True, max_length=200)),
                ("description", models.TextField(blank=True, default="")),
                (
                    "registration_token_hash",
                    models.CharField(
                        blank=True, db_index=True, max_length=64, null=True
                    ),
                ),
                (
                    "api_key_hash",
                    models.CharField(
                        blank=True, db_index=True, max_length=64, null=True
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("offline", "Offline"),
                            ("idle", "Idle"),
                            ("active", "Active"),
                            ("suspended", "Suspended"),
                        ],
                        db_index=True,
                        default="offline",
                        max_length=16,
                    ),
                ),
                (
                    "os",
                    models.CharField(
                        choices=[
                            ("linux", "Linux"),
                            ("macos", "macOS"),
                            ("windows", "Windows"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "arch",
                    models.CharField(
                        choices=[
                            ("amd64", "amd64 (x86_64)"),
                            ("arm64", "arm64 (Apple Silicon / Graviton)"),
                        ],
                        max_length=8,
                    ),
                ),
                ("labels", models.JSONField(blank=True, default=list)),
                ("last_heartbeat_at", models.DateTimeField(blank=True, null=True)),
                ("version_string", models.CharField(blank=True, default="", max_length=64)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="runners",
                        to="astrolift_identity.organization",
                    ),
                ),
                (
                    "current_job_run",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="executing_runners",
                        to="astrolift_pipelines.jobrun",
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
                    "deleted_by",
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
                "abstract": False,
            },
        ),
        # --- Add Runner FK to JobRun now that Runner exists ---
        migrations.AddField(
            model_name="jobrun",
            name="claimed_by_runner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="job_runs",
                to="astrolift_pipelines.runner",
            ),
        ),
        migrations.AddIndex(
            model_name="jobrun",
            index=models.Index(
                fields=["organization", "status"],
                name="astrolift_p_org_jobrun_status_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="runner",
            constraint=models.UniqueConstraint(
                condition=models.Q(deleted_at__isnull=True),
                fields=["organization", "slug"],
                name="runner_slug_unique_per_org_active",
            ),
        ),
    ]
