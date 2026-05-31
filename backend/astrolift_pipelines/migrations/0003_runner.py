"""Add Runner model — self-hosted runner for pipeline job execution (#81)."""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models

import core.fields


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_pipelines", "0002_pipeline_registered_app"),
        ("astrolift_identity", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Runner",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("guid", core.fields.UUIDv7Field(db_index=True, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("version", models.IntegerField(default=0)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("deleted_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to="auth.user",
                )),
                ("name", models.CharField(max_length=200)),
                ("slug", models.SlugField(max_length=200)),
                ("registration_token_hash", models.CharField(blank=True, default="", max_length=64)),
                ("api_key_hash", models.CharField(blank=True, default="", max_length=64)),
                ("status", models.CharField(
                    choices=[("offline", "Offline"), ("idle", "Idle"), ("active", "Active — running a job"), ("suspended", "Suspended")],
                    default="offline", max_length=32,
                )),
                ("last_heartbeat_at", models.DateTimeField(blank=True, null=True)),
                ("os", models.CharField(
                    choices=[("linux", "Linux"), ("macos", "macOS"), ("windows", "Windows")],
                    default="linux", max_length=16,
                )),
                ("arch", models.CharField(
                    choices=[("amd64", "x86-64 (amd64)"), ("arm64", "ARM64")],
                    default="amd64", max_length=16,
                )),
                ("labels", models.JSONField(default=list)),
                ("version_string", models.CharField(blank=True, default="", max_length=64)),
                ("organization", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="runners",
                    to="astrolift_identity.organization",
                )),
                ("current_job_run", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="claimed_by_runner",
                    to="astrolift_pipelines.jobrun",
                )),
            ],
            options={
                "unique_together": {("organization", "slug")},
                "indexes": [models.Index(fields=["organization", "status"], name="runner_org_status_idx")],
            },
        ),
    ]
