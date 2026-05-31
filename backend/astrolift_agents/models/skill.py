"""
Skill + ToolDef — reusable building blocks injected into a Brief at
assembly time (issue #42, Agent Dispatch Layer — Skill Registry).

A Skill is a versioned unit of instructions/scripts that an agent
receives at boot.  A ToolDef describes a callable tool the agent can
invoke; it is linked to Tasks at dispatch time.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class Skill(BaseCoreModel):
    # Null org → global skill, readable by all orgs, admin-writable only.
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="skills",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=255)
    slug = models.CharField(max_length=128)
    description = models.TextField(blank=True, default="")
    # Instructions / script body surfaced to the agent at boot.
    content = models.TextField(blank=True, default="")
    # JSON list of pip/npm/system dependency specs.
    dependencies = models.JSONField(default=list, blank=True)
    # SHA-256 of ``content``, hex-encoded.  Auto-updated by the
    # assembly service; used for dedup — no separate SkillVersion table
    # (content_hash is sufficient for now).
    content_hash = models.CharField(max_length=64, blank=True, default="")
    # Monotonically increasing on every content change.
    # Named ``skill_version`` to avoid shadowing ``BaseCoreModel.version``
    # (the optimistic-concurrency counter from TrackingMixin).
    skill_version = models.PositiveIntegerField(default=1)
    is_global = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="skill_slug_unique_active_per_org",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "is_active"], name="skill_org_active_idx"),
            models.Index(fields=["is_global"], name="skill_is_global_idx"),
        ]

    def __str__(self) -> str:
        return f"Skill {self.slug} v{self.skill_version}"


class ToolDef(BaseCoreModel):
    class Adapter(models.TextChoices):
        PYTHON_FN = "python_fn"
        HTTP_ENDPOINT = "http_endpoint"
        MCP_SERVER = "mcp_server"

    skill = models.ForeignKey(
        Skill,
        related_name="tool_defs",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=255)
    slug = models.CharField(max_length=128)
    description = models.TextField(blank=True, default="")
    # JSON Schema describing the tool's input.
    input_schema = models.JSONField(default=dict, blank=True)
    # JSON Schema describing the tool's output.
    output_schema = models.JSONField(default=dict, blank=True)
    adapter = models.CharField(
        max_length=32,
        choices=Adapter.choices,
        default=Adapter.PYTHON_FN,
    )
    # Dotted Python path, HTTP URL, or MCP server address depending on adapter.
    handler_ref = models.CharField(max_length=1024, blank=True, default="")
    # Adapter-specific config (e.g. HTTP headers, auth scheme, timeout).
    implementation_config = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["skill", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="tooldef_slug_unique_active_per_skill",
            ),
        ]

    def __str__(self) -> str:
        return f"ToolDef {self.slug} ({self.adapter})"


class BriefSkillRef(BaseCoreModel):
    """Join: which Skills are included in a Brief."""

    brief = models.ForeignKey(
        "astrolift_agents.Brief",
        related_name="skill_refs",
        on_delete=models.CASCADE,
    )
    skill = models.ForeignKey(
        Skill,
        related_name="brief_refs",
        on_delete=models.PROTECT,
    )
    # Snapshot of the skill version at assembly time so the Brief is
    # stable even if the Skill is updated afterward.
    skill_version = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["brief", "skill"],
                name="briefskillref_unique_per_brief",
            ),
        ]

    def __str__(self) -> str:
        return f"BriefSkillRef brief={self.brief_id} skill={self.skill_id}"


class WorkloadToolDef(BaseCoreModel):
    """Join: which ToolDefs a Workload exposes for cross-workload invocation."""

    workload = models.ForeignKey(
        "astrolift_registry.Workload",
        related_name="tool_defs",
        on_delete=models.CASCADE,
    )
    tool_def = models.ForeignKey(
        ToolDef,
        related_name="workload_refs",
        on_delete=models.CASCADE,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workload", "tool_def"],
                name="workloadtooldef_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"WorkloadToolDef workload={self.workload_id} tool_def={self.tool_def_id}"


class TaskToolDef(BaseCoreModel):
    """Join: which ToolDefs are active for a given AgentRun dispatch."""

    agent_run = models.ForeignKey(
        "astrolift_lifecycle.AgentRun",
        related_name="task_tool_defs",
        on_delete=models.CASCADE,
    )
    tool_def = models.ForeignKey(
        ToolDef,
        related_name="task_refs",
        on_delete=models.CASCADE,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["agent_run", "tool_def"],
                name="tasktooldef_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"TaskToolDef run={self.agent_run_id} tool_def={self.tool_def_id}"
