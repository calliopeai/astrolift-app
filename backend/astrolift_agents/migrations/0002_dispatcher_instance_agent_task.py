"""
Add DispatcherInstance and AgentTask models.

DispatcherInstance: registered Dispatch Service endpoints with cloud/
backend/capability-label routing and heartbeat tracking.

AgentTask: discrete unit of agent dispatch with a DRAFT → QUEUED →
PROVISIONING → RUNNING → COMPLETED|FAILED|TIMED_OUT|CANCELLED state
machine.

Refs: #43, #44.
"""

import core.fields.uuid_v7
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_agents", "0001_initial"),
        ("astrolift_clusters", "0001_initial"),
        ("astrolift_identity", "0001_initial"),
        ("astrolift_registry", "0022_workload_kind_agent_dispatch_fields"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ------------------------------------------------------------------
        # DispatcherInstance
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="DispatcherInstance",
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
                        db_index=True,
                        default=core.fields.uuid_v7.uuid7,
                        editable=False,
                        unique=True,
                    ),
                ),
                ("name", models.CharField(max_length=200)),
                ("slug", models.SlugField(unique=True)),
                ("endpoint", models.URLField()),
                ("api_key_hash", models.CharField(blank=True, max_length=64)),
                (
                    "cloud",
                    models.CharField(
                        choices=[
                            ("aws", "Aws"),
                            ("gcp", "Gcp"),
                            ("azure", "Azure"),
                            ("k8s_native", "K8S Native"),
                            ("local", "Local"),
                        ],
                        max_length=32,
                    ),
                ),
                ("region", models.CharField(blank=True, default="", max_length=64)),
                (
                    "backend",
                    models.CharField(
                        choices=[
                            ("k8s_job", "K8S Job"),
                            ("ecs_task", "Ecs Task"),
                            ("local_docker", "Local Docker"),
                        ],
                        max_length=32,
                    ),
                ),
                ("capability_labels", models.JSONField(default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("active", "Active"),
                            ("suspended", "Suspended"),
                            ("rejected", "Rejected"),
                        ],
                        default="pending",
                        max_length=32,
                    ),
                ),
                ("last_heartbeat_at", models.DateTimeField(blank=True, null=True)),
                ("heartbeat_ttl_seconds", models.IntegerField(default=30)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="dispatcher_instances",
                        to="astrolift_identity.organization",
                    ),
                ),
                (
                    "tenant_cluster",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dispatcher_instances",
                        to="astrolift_clusters.tenantcluster",
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
        ),
        # ------------------------------------------------------------------
        # AgentTask
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="AgentTask",
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
                        db_index=True,
                        default=core.fields.uuid_v7.uuid7,
                        editable=False,
                        unique=True,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("queued", "Queued"),
                            ("provisioning", "Provisioning"),
                            ("running", "Running"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                            ("timed_out", "Timed Out"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="draft",
                        max_length=32,
                    ),
                ),
                ("external_id", models.CharField(blank=True, default="", max_length=255)),
                ("callback_url", models.URLField(blank=True, default="")),
                ("timeout_seconds", models.IntegerField(default=300)),
                ("result", models.JSONField(blank=True, null=True)),
                ("failure", models.JSONField(blank=True, null=True)),
                ("queued_at", models.DateTimeField(blank=True, null=True)),
                ("provisioning_at", models.DateTimeField(blank=True, null=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("telemetry_key", models.CharField(blank=True, default="", max_length=512)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agent_tasks",
                        to="astrolift_identity.organization",
                    ),
                ),
                (
                    "team",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="agent_tasks",
                        to="astrolift_identity.team",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="agent_tasks",
                        to="astrolift_identity.project",
                    ),
                ),
                (
                    "agent_definition",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="agent_tasks",
                        to="astrolift_registry.workload",
                    ),
                ),
                (
                    "brief",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="agent_tasks",
                        to="astrolift_agents.brief",
                    ),
                ),
                (
                    "dispatcher",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="agent_tasks",
                        to="astrolift_agents.dispatcherinstance",
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
        ),
        # ------------------------------------------------------------------
        # Indexes
        # ------------------------------------------------------------------
        migrations.AddIndex(
            model_name="dispatcherinstance",
            index=models.Index(
                fields=["organization", "status"],
                name="dispatcher_org_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="agenttask",
            index=models.Index(
                fields=["organization", "status"],
                name="agent_task_org_status_idx",
            ),
        ),
    ]
