"""
Add FunctionInvocation model — execution history for function workload kind.

FunctionInvocation records one reactive container execution triggered by an
HTTP request, queue message, webhook, or platform event (#802).

Additive new table. No existing data is affected.
"""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models

import core.fields


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_lifecycle", "0024_task_run_agent_run"),
        ("astrolift_registry", "0024_workload_kind_function"),
    ]

    operations = [
        migrations.CreateModel(
            name="FunctionInvocation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("guid", core.fields.UUIDv7Field(db_index=True, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("version", models.IntegerField(default=0)),
                (
                    "trigger_source",
                    models.CharField(
                        choices=[("http", "Http"), ("event", "Event"), ("schedule", "Schedule")],
                        default="http",
                        max_length=32,
                    ),
                ),
                ("http_method", models.CharField(blank=True, default="", max_length=10)),
                ("http_path", models.TextField(blank=True, default="")),
                ("http_status_code", models.IntegerField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("running", "Running"),
                            ("success", "Success"),
                            ("error", "Error"),
                        ],
                        default="pending",
                        max_length=32,
                    ),
                ),
                ("error_message", models.TextField(blank=True, default="")),
                ("duration_ms", models.IntegerField(blank=True, null=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("k8s_pod_name", models.CharField(blank=True, default="", max_length=253)),
                (
                    "app_environment",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="function_invocations",
                        to="astrolift_lifecycle.appenvironment",
                    ),
                ),
                (
                    "workload",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="function_invocations",
                        to="astrolift_registry.workload",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["workload", "-created_at"],
                        name="funcinv_workload_created_idx",
                    ),
                ],
            },
        ),
    ]
