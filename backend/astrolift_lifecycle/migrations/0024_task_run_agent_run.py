"""
Add TaskRun and AgentRun models — execution history for task and agent workload kinds.

TaskRun records one-off container executions triggered by operators (#793, #803).
AgentRun records AI agent dispatch executions (#795, #804).

Both are additive new tables. No existing data is affected.
"""

from __future__ import annotations

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import core.fields


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_lifecycle", "0023_devenvironment"),
        ("astrolift_registry", "0022_workload_kind_agent_dispatch_fields"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TaskRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("guid", core.fields.UUIDv7Field(db_index=True, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("version", models.IntegerField(default=0)),
                (
                    "trigger_kind",
                    models.CharField(
                        choices=[("manual", "Manual"), ("api", "Api"), ("workflow", "Workflow")],
                        default="manual",
                        max_length=32,
                    ),
                ),
                ("command", models.JSONField(default=list)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("running", "Running"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="pending",
                        max_length=32,
                    ),
                ),
                ("exit_code", models.IntegerField(blank=True, null=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("duration_seconds", models.IntegerField(blank=True, null=True)),
                ("k8s_job_name", models.CharField(blank=True, default="", max_length=255)),
                (
                    "app_environment",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="task_runs",
                        to="astrolift_lifecycle.appenvironment",
                    ),
                ),
                (
                    "triggered_by_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="task_runs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workload",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="task_runs",
                        to="astrolift_registry.workload",
                    ),
                ),
            ],
            options={"indexes": [models.Index(fields=["workload", "-created_at"], name="taskrun_workload_created_idx")]},
        ),
        migrations.CreateModel(
            name="AgentRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("guid", core.fields.UUIDv7Field(db_index=True, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("version", models.IntegerField(default=0)),
                (
                    "trigger_kind",
                    models.CharField(
                        choices=[
                            ("manual", "Manual"),
                            ("api", "Api"),
                            ("scheduled", "Scheduled"),
                            ("event", "Event"),
                        ],
                        default="manual",
                        max_length=32,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("running", "Running"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="pending",
                        max_length=32,
                    ),
                ),
                ("input", models.JSONField(blank=True, null=True)),
                ("output", models.JSONField(blank=True, null=True)),
                ("reasoning_trace_url", models.CharField(blank=True, default="", max_length=1024)),
                ("tool_calls_count", models.IntegerField(default=0)),
                ("retry_count", models.IntegerField(default=0)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("duration_seconds", models.IntegerField(blank=True, null=True)),
                ("k8s_pod_name", models.CharField(blank=True, default="", max_length=255)),
                ("result_ttl_hours", models.IntegerField(default=72)),
                (
                    "app_environment",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agent_runs",
                        to="astrolift_lifecycle.appenvironment",
                    ),
                ),
                (
                    "triggered_by_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="agent_runs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workload",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="agent_runs",
                        to="astrolift_registry.workload",
                    ),
                ),
            ],
            options={"indexes": [models.Index(fields=["workload", "-created_at"], name="agentrun_workload_created_idx")]},
        ),
    ]
