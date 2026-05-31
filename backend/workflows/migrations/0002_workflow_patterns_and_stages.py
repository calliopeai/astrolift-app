"""Add agent workflow patterns and stage execution tracking (#45, #46).

WorkflowDefinition gains a ``pattern_kind`` field describing the
multi-agent composition pattern (single, chained, fan_out, …).

WorkflowStage defines the ordered stages within a workflow definition —
what kind of step (agent dispatch, human gate, checkpoint, aggregation),
which agent workload, skill refs, fan-out count, failure policy, and
timeout.

WorkflowStageExecution is the per-run execution record for one stage.
One row per dispatched task (multiple rows per FAN_OUT stage). FK to
WorkflowRun (mirror), WorkflowStage (definition), and optionally
AgentRun (dispatch record). Append-only after reaching a terminal
status — enforced at the application layer.
"""

from __future__ import annotations

import core.fields
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workflows", "0001_initial"),
        ("astrolift_registry", "0024_workload_kind_function"),
        ("astrolift_operations", "0015_alertrule_managed_service"),
        ("astrolift_lifecycle", "0024_task_run_agent_run"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── #45: PatternKind on WorkflowDefinition ──────────────────────────
        migrations.AddField(
            model_name="workflowdefinition",
            name="pattern_kind",
            field=models.CharField(
                choices=[
                    ("single", "Single"),
                    ("chained", "Chained"),
                    ("fan_out", "Fan Out"),
                    ("supervisor_worker", "Supervisor Worker"),
                    ("review_loop", "Review Loop"),
                    ("advisor", "Advisor"),
                ],
                db_index=True,
                default="single",
                help_text="Multi-agent composition pattern for this workflow definition.",
                max_length=32,
            ),
        ),
        # ── #45: WorkflowStage ───────────────────────────────────────────────
        migrations.CreateModel(
            name="WorkflowStage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("guid", core.fields.UUIDv7Field(db_index=True, unique=True)),
                ("version", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("order", models.IntegerField(
                    help_text="0-based position of this stage within the workflow sequence.",
                )),
                ("kind", models.CharField(
                    choices=[
                        ("agent_dispatch", "Agent Dispatch"),
                        ("human_gate", "Human Gate"),
                        ("checkpoint", "Checkpoint"),
                        ("aggregation", "Aggregation"),
                    ],
                    default="agent_dispatch",
                    max_length=32,
                )),
                ("skill_refs", models.JSONField(
                    blank=True,
                    default=list,
                    help_text="Ordered list of Skill slugs injected at dispatch.",
                )),
                ("fan_out_count", models.IntegerField(
                    blank=True,
                    null=True,
                    help_text="Max parallel tasks for fan_out stages. Null → dynamic from prior output.",
                )),
                ("on_failure", models.CharField(
                    choices=[
                        ("fail", "Fail"),
                        ("retry", "Retry"),
                        ("skip", "Skip"),
                        ("escalate", "Escalate"),
                    ],
                    default="fail",
                    max_length=16,
                    help_text="What to do if this stage fails.",
                )),
                ("timeout_seconds", models.IntegerField(
                    default=300,
                    help_text="Maximum wall-clock time for this stage before it times out.",
                )),
                ("definition", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="stages",
                    to="workflows.workflowdefinition",
                )),
                ("agent_definition", models.ForeignKey(
                    blank=True,
                    help_text="Agent workload to dispatch at this stage (kind=agent_dispatch only).",
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="workflow_stages",
                    to="astrolift_registry.workload",
                )),
                ("created_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                    verbose_name="Creator",
                )),
                ("updated_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("deleted_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "ordering": ["definition", "order"],
            },
        ),
        migrations.AddConstraint(
            model_name="workflowstage",
            constraint=models.UniqueConstraint(
                fields=["definition", "order"],
                name="workflowstage_definition_order_unique",
            ),
        ),
        migrations.AddIndex(
            model_name="workflowstage",
            index=models.Index(fields=["definition", "order"], name="wfstage_def_order_idx"),
        ),
        # ── #46: WorkflowStageExecution ──────────────────────────────────────
        migrations.CreateModel(
            name="WorkflowStageExecution",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("guid", core.fields.UUIDv7Field(db_index=True, unique=True)),
                ("version", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("status", models.CharField(
                    choices=[
                        ("pending", "Pending"),
                        ("running", "Running"),
                        ("completed", "Completed"),
                        ("failed", "Failed"),
                        ("skipped", "Skipped"),
                        ("escalated", "Escalated"),
                        ("cancelled", "Cancelled"),
                    ],
                    db_index=True,
                    default="pending",
                    max_length=16,
                )),
                ("attempt_number", models.IntegerField(
                    default=1,
                    help_text="1-based retry count — incremented each time the stage is retried.",
                )),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("output", models.JSONField(
                    blank=True,
                    null=True,
                    help_text="Stage result payload passed as input to the next stage.",
                )),
                ("failure", models.JSONField(
                    blank=True,
                    null=True,
                    help_text="Structured failure details (error kind, message, stack excerpt).",
                )),
                ("error_message", models.TextField(blank=True, default="")),
                ("workflow_run", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="stage_executions",
                    to="astrolift_operations.workflowrun",
                )),
                ("stage", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="executions",
                    to="workflows.workflowstage",
                )),
                ("agent_run", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="stage_executions",
                    to="astrolift_lifecycle.agentrun",
                )),
                ("fan_out_sources", models.ManyToManyField(
                    blank=True,
                    related_name="aggregated_by",
                    to="workflows.workflowstageexecution",
                )),
                ("created_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                    verbose_name="Creator",
                )),
                ("updated_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("deleted_by", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="+",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
        ),
        migrations.AddIndex(
            model_name="workflowstageexecution",
            index=models.Index(
                fields=["workflow_run", "stage"],
                name="wfstageexec_run_stage_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="workflowstageexecution",
            index=models.Index(
                fields=["workflow_run", "-started_at"],
                name="wfstageexec_run_started_idx",
            ),
        ),
    ]
