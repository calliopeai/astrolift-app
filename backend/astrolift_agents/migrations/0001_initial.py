"""Initial migration for astrolift_agents app.

Creates: Brief, Skill, ToolDef, BriefSkillRef, WorkloadToolDef, TaskToolDef.
"""

import core.fields.uuid_v7
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("astrolift_identity", "0001_initial"),
        ("astrolift_lifecycle", "0001_initial"),
        ("astrolift_registry", "0025_workload_agent_variant_runtime"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ------------------------------------------------------------------
        # Brief
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="Brief",
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
                ("content_hash", models.CharField(max_length=64, unique=True)),
                ("storage_key", models.CharField(blank=True, default="", max_length=1024)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("assembling", "Assembling"),
                            ("ready", "Ready"),
                            ("revoked", "Revoked"),
                        ],
                        default="assembling",
                        max_length=16,
                    ),
                ),
                ("manifest_snapshot", models.JSONField(blank=True, default=dict)),
                ("secrets_refs", models.JSONField(blank=True, default=list)),
                ("context", models.JSONField(blank=True, default=dict)),
                ("ttl_seconds", models.PositiveIntegerField(default=3600)),
                ("assembled_at", models.DateTimeField(blank=True, null=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="briefs",
                        to="astrolift_identity.organization",
                    ),
                ),
                (
                    "registered_app",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="briefs",
                        to="astrolift_registry.registeredapp",
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
        # Skill
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="Skill",
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
                ("name", models.CharField(max_length=255)),
                ("slug", models.CharField(max_length=128)),
                ("description", models.TextField(blank=True, default="")),
                ("content", models.TextField(blank=True, default="")),
                ("dependencies", models.JSONField(blank=True, default=list)),
                ("content_hash", models.CharField(blank=True, default="", max_length=64)),
                ("skill_version", models.PositiveIntegerField(default=1)),
                ("is_global", models.BooleanField(default=False)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "organization",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="skills",
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
        # ToolDef
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="ToolDef",
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
                ("name", models.CharField(max_length=255)),
                ("slug", models.CharField(max_length=128)),
                ("description", models.TextField(blank=True, default="")),
                ("input_schema", models.JSONField(blank=True, default=dict)),
                ("output_schema", models.JSONField(blank=True, default=dict)),
                (
                    "adapter",
                    models.CharField(
                        choices=[
                            ("python_fn", "Python Fn"),
                            ("http_endpoint", "Http Endpoint"),
                            ("mcp_server", "Mcp Server"),
                        ],
                        default="python_fn",
                        max_length=32,
                    ),
                ),
                ("handler_ref", models.CharField(blank=True, default="", max_length=1024)),
                ("implementation_config", models.JSONField(blank=True, default=dict)),
                (
                    "skill",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tool_defs",
                        to="astrolift_agents.skill",
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
        # BriefSkillRef
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="BriefSkillRef",
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
                ("skill_version", models.PositiveIntegerField()),
                (
                    "brief",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="skill_refs",
                        to="astrolift_agents.brief",
                    ),
                ),
                (
                    "skill",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="brief_refs",
                        to="astrolift_agents.skill",
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
        # WorkloadToolDef
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="WorkloadToolDef",
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
                    "workload",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tool_defs",
                        to="astrolift_registry.workload",
                    ),
                ),
                (
                    "tool_def",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="workload_refs",
                        to="astrolift_agents.tooldef",
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
        # TaskToolDef
        # ------------------------------------------------------------------
        migrations.CreateModel(
            name="TaskToolDef",
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
                    "agent_run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="task_tool_defs",
                        to="astrolift_lifecycle.agentrun",
                    ),
                ),
                (
                    "tool_def",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="task_refs",
                        to="astrolift_agents.tooldef",
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
            model_name="brief",
            index=models.Index(
                fields=["organization", "status"],
                name="brief_org_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="brief",
            index=models.Index(
                fields=["content_hash"],
                name="brief_content_hash_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="skill",
            index=models.Index(
                fields=["organization", "is_active"],
                name="skill_org_active_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="skill",
            index=models.Index(
                fields=["is_global"],
                name="skill_is_global_idx",
            ),
        ),
        # ------------------------------------------------------------------
        # Constraints
        # ------------------------------------------------------------------
        migrations.AddConstraint(
            model_name="skill",
            constraint=models.UniqueConstraint(
                condition=models.Q(deleted_at__isnull=True),
                fields=("organization", "slug"),
                name="skill_slug_unique_active_per_org",
            ),
        ),
        migrations.AddConstraint(
            model_name="tooldef",
            constraint=models.UniqueConstraint(
                condition=models.Q(deleted_at__isnull=True),
                fields=("skill", "slug"),
                name="tooldef_slug_unique_active_per_skill",
            ),
        ),
        migrations.AddConstraint(
            model_name="briefskillref",
            constraint=models.UniqueConstraint(
                fields=("brief", "skill"),
                name="briefskillref_unique_per_brief",
            ),
        ),
        migrations.AddConstraint(
            model_name="workloadtooldef",
            constraint=models.UniqueConstraint(
                fields=("workload", "tool_def"),
                name="workloadtooldef_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="tasktooldef",
            constraint=models.UniqueConstraint(
                fields=("agent_run", "tool_def"),
                name="tasktooldef_unique",
            ),
        ),
    ]
