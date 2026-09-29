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
    # Agent runtime this skill targets: "claude", "codex", or "any".
    agent_type = models.CharField(max_length=50, blank=True, default="")
    # Scaffolding categories used to surface the skill, e.g.
    # ["code-review", "infra-ops"].
    scaffolding_tags = models.JSONField(default=list, blank=True)

    class SourceKind(models.TextChoices):
        # Where the skill came from (#2155), for the catalog's Imported view.
        # Empty is a skill written in the UI or seeded by the platform.
        REPO_IMPORT = "repo_import"  # importSkillsFromRepo (an astrolift.toml library)
        AGENT_REPO = "agent_repo"  # a local skill folder in a registered agent's repo
        ORG_REPO = "org_repo"  # an org-registered skill repo (spec 39d)
        CATALOGUE = "catalogue"  # the built-in skill catalogue

    source_kind = models.CharField(max_length=16, choices=SourceKind.choices, blank=True, default="")
    # The pointer it was imported from, e.g. ``owner/repo@main:skills`` or
    # ``alias/path@ref``; empty when ``source_kind`` is.
    source_ref = models.CharField(max_length=512, blank=True, default="")

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
    # Shell commands this tool exposes, e.g. ["git", "gh"].
    commands = models.JSONField(default=list, blank=True)
    # System packages the tool needs, e.g.
    # [{"manager": "apt", "name": "git"}].
    required_packages = models.JSONField(default=list, blank=True)
    # Coarse grouping: dev | cloud | k8s | data | infra | custom.
    capability_group = models.CharField(max_length=50, blank=True, default="")
    # Agent runtimes this tool binds to: ["claude", "codex"] or ["*"].
    agent_type_bindings = models.JSONField(default=list, blank=True)
    # True = pre-installed in the image; False = installed on demand.
    is_builtin = models.BooleanField(default=True)

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


class AgentSkillRef(BaseCoreModel):
    """Join: which Skills are attached to an agent Workload's definition.

    The definitional counterpart to :class:`BriefSkillRef`. Where a
    ``BriefSkillRef`` snapshots a Skill into an already-assembled Brief,
    an ``AgentSkillRef`` records the Skills an ``agent`` Workload carries
    at the definition level (set at registration in Phase 3); the
    assembly service reads these to build the agent's Brief.
    """

    workload = models.ForeignKey(
        "astrolift_registry.Workload",
        related_name="agent_skill_refs",
        on_delete=models.CASCADE,
    )
    skill = models.ForeignKey(
        Skill,
        related_name="agent_refs",
        on_delete=models.PROTECT,
    )
    # Ordering within the agent's skill set (ascending). Skills with the
    # same position fall back to insertion order.
    position = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workload", "skill"],
                name="agentskillref_unique_per_workload",
            ),
        ]

    def __str__(self) -> str:
        return f"AgentSkillRef workload={self.workload_id} skill={self.skill_id}"


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
