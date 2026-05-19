# Hand-written for #734 — per-project trigger-token model for non-GitHub
# workflow dispatch (GitLab pipeline trigger, Bitbucket API key, Gitea
# workflow-dispatch token). Dispatcher wiring lives in #532; this
# migration only creates the table.

import core.fields.uuid_v7
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_scm", "0009_sourceconnection_app_client_id"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SourceTriggerToken",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("guid", core.fields.uuid_v7.UUIDv7Field(db_index=True, default=core.fields.uuid_v7.uuid7, editable=False, unique=True)),
                ("repo_full_name", models.CharField(max_length=255)),
                ("kind", models.CharField(
                    choices=[
                        ("gitlab_pipeline_trigger", "Gitlab Pipeline Trigger"),
                        ("bitbucket_pipeline_api_key", "Bitbucket Pipeline Api Key"),
                        ("gitea_workflow_dispatch", "Gitea Workflow Dispatch"),
                    ],
                    max_length=48,
                )),
                ("secret_backend_kind", models.CharField(default="local_fernet", max_length=32)),
                ("secret_ciphertext", models.BinaryField(blank=True, default=b"")),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                    verbose_name="Creator",
                )),
                ("deleted_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("updated_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("source_connection", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="trigger_tokens",
                    to="astrolift_scm.sourceconnection",
                )),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["source_connection", "repo_full_name", "-created_at"],
                        name="trigger_token_conn_repo_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(deleted_at__isnull=True),
                        fields=("source_connection", "repo_full_name", "kind"),
                        name="trigger_token_unique_active_per_conn_repo_kind",
                    ),
                ],
            },
        ),
    ]
