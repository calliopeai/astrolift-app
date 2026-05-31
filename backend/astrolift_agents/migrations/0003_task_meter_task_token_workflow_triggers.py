"""Add TaskMeteringRecord, TaskToken, WorkflowSchedule, WorkflowWebhook.

These models were implemented by the dispatch-service and workflow-triggers
agents (#56, #60, #62) but their migration was omitted from the initial
agent-layer merge. All four are additive new tables.
"""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_agents", "0002_dispatcher_instance_agent_task"),
        # TaskMeteringRecord and TaskToken FK → AgentRun
        ("astrolift_lifecycle", "0026_merge_0025_agentrun_log_excerpt_0025_function_invocation"),
        # WorkflowSchedule and WorkflowWebhook FK → WorkflowDefinition
        ("workflows", "0002_workflow_patterns_and_stages"),
    ]

    operations = [
        # ── TaskMeteringRecord ─────────────────────────────────────────────
        migrations.CreateModel(
            name="TaskMeteringRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                (
                    "wall_seconds",
                    models.IntegerField(
                        blank=True,
                        help_text="ended_at - started_at in whole seconds; null if task never started.",
                        null=True,
                    ),
                ),
                (
                    "cpu_seconds",
                    models.DecimalField(
                        blank=True,
                        decimal_places=3,
                        help_text="CPU-time consumed (as reported by the runtime or estimated).",
                        max_digits=14,
                        null=True,
                    ),
                ),
                (
                    "memory_peak_mb",
                    models.IntegerField(
                        blank=True,
                        help_text="Peak RSS in MB; null when the runtime doesn't expose it.",
                        null=True,
                    ),
                ),
                (
                    "token_input",
                    models.IntegerField(
                        blank=True,
                        help_text="LLM input tokens consumed; null for non-LLM tasks.",
                        null=True,
                    ),
                ),
                (
                    "token_output",
                    models.IntegerField(
                        blank=True,
                        help_text="LLM output tokens generated; null for non-LLM tasks.",
                        null=True,
                    ),
                ),
                (
                    "metering_source",
                    models.CharField(
                        choices=[
                            ("k8s_metrics", "Kubernetes Metrics"),
                            ("ecs_metadata", "ECS Task Metadata"),
                            ("wall_time_estimate", "Wall-Time Estimate"),
                            ("agent_reported", "Agent-Reported"),
                        ],
                        default="wall_time_estimate",
                        max_length=32,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "agent_run",
                    models.OneToOneField(
                        db_index=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="metering_record",
                        to="astrolift_lifecycle.agentrun",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["created_at"], name="taskmeter_created_idx"),
                ],
            },
        ),
        # ── TaskToken ──────────────────────────────────────────────────────
        migrations.CreateModel(
            name="TaskToken",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                (
                    "token_hash",
                    models.CharField(
                        db_index=True,
                        help_text="SHA-256 hex digest of the plaintext token; never store plaintext.",
                        max_length=64,
                        unique=True,
                    ),
                ),
                ("issued_at", models.DateTimeField(auto_now_add=True)),
                (
                    "expires_at",
                    models.DateTimeField(
                        db_index=True,
                        help_text="Hard expiry; at most 72h after issuance.",
                    ),
                ),
                (
                    "revoked_at",
                    models.DateTimeField(
                        blank=True,
                        db_index=True,
                        help_text="Set on Task terminal transition; null while task is live.",
                        null=True,
                    ),
                ),
                (
                    "agent_run",
                    models.OneToOneField(
                        db_index=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="task_token",
                        to="astrolift_lifecycle.agentrun",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["expires_at"], name="tasktoken_expires_idx"),
                ],
            },
        ),
        # ── WorkflowSchedule ───────────────────────────────────────────────
        migrations.CreateModel(
            name="WorkflowSchedule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                (
                    "cron_expression",
                    models.CharField(
                        help_text="Standard 5-field cron expression (e.g. '0 9 * * 1-5').",
                        max_length=100,
                    ),
                ),
                ("timezone", models.CharField(default="UTC", max_length=64)),
                (
                    "input_template",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="Static input payload passed to each workflow instance.",
                    ),
                ),
                ("enabled", models.BooleanField(db_index=True, default=True)),
                (
                    "temporal_schedule_id",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Temporal schedule ID; empty when Temporal is disabled.",
                        max_length=200,
                    ),
                ),
                ("last_triggered_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "workflow_definition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="schedules",
                        to="workflows.workflowdefinition",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["workflow_definition", "enabled"],
                        name="wfschedule_defn_enabled_idx",
                    ),
                ],
            },
        ),
        # ── WorkflowWebhook ────────────────────────────────────────────────
        migrations.CreateModel(
            name="WorkflowWebhook",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                (
                    "slug",
                    models.SlugField(
                        help_text="URL-safe identifier used in the endpoint path.",
                        unique=True,
                    ),
                ),
                (
                    "secret_hash",
                    models.CharField(
                        help_text="SHA-256 hex digest of the signing secret; never store plaintext.",
                        max_length=64,
                    ),
                ),
                (
                    "input_mapping",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text="JSONPath / key-mapping spec that maps incoming payload fields to the workflow input schema.",
                    ),
                ),
                ("enabled", models.BooleanField(db_index=True, default=True)),
                ("last_triggered_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "workflow_definition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="webhooks",
                        to="workflows.workflowdefinition",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["slug", "enabled"],
                        name="wfwebhook_slug_enabled_idx",
                    ),
                ],
            },
        ),
    ]
