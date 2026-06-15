"""
AgentEnvironmentSpec — a reusable, org-scoped recipe for the container
environment an agent task runs in.

Captures the image, agent runtime, tool preset, capability toggles, and
the *references* to secrets needed at launch — never the secret values
themselves.  The dispatcher resolves ``secret_refs`` to live values at
spawn time so this row stays safe to read and replicate.

See the agent platform foundation (Agent Environment Spec) work.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class AgentEnvironmentSpec(BaseCoreModel):
    class AgentType(models.TextChoices):
        CLAUDE = "claude"
        CODEX = "codex"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="agent_environment_specs",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=128)
    # Explicit image ref (e.g. a pinned ECR/private URI) the dispatcher pulls
    # for this spec. When set it WINS over ``runtime``; leave blank to resolve
    # the image from the runtime catalog instead.
    image_tag = models.CharField(max_length=512, blank=True, default="")
    # Catalog runtime short-name (e.g. "claude", "aider"). Resolves to the
    # public image ``docker.io/calliopeai/astrolift-agent-<name>`` via
    # astrolift_agents.runtime_catalog when ``image_tag`` is blank. Empty =
    # no catalog runtime (the dispatcher falls back to the workload image).
    runtime = models.CharField(max_length=64, blank=True, default="")
    agent_type = models.CharField(max_length=50, choices=AgentType.choices)
    # Named tool bundle, e.g. "dev+k8s".  Empty = image defaults only.
    tool_preset = models.CharField(max_length=128, blank=True, default="")
    allow_install = models.BooleanField(default=False)
    vnc_enabled = models.BooleanField(default=False)
    # Secret URIs only — values resolved by the dispatcher at launch time.
    # Never store values.  Format:
    #   [{"uri": "arn:aws:secretsmanager:...", "env_var": "GITHUB_TOKEN"}]
    secret_refs = models.JSONField(default=list, blank=True)
    # Non-sensitive env vars stored directly.
    env_vars = models.JSONField(default=dict, blank=True)
    # "owner/repo" holding the astrolift.toml config for this environment.
    config_repo = models.CharField(max_length=512, blank=True, default="")
    config_branch = models.CharField(max_length=128, default="main")
    # Repo-relative path to this agent's manifest (e.g. "agents/foo/astrolift.toml"
    # or the directory "agents/foo"). Empty selects the root-most astrolift.toml,
    # so a single repo can hold many agents.
    config_manifest_path = models.CharField(max_length=512, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="agent_env_spec_slug_unique_active_per_org",
            ),
        ]
        indexes = [
            models.Index(fields=["organization"], name="agent_env_spec_org_idx"),
        ]

    def __str__(self) -> str:
        return f"AgentEnvironmentSpec {self.slug} ({self.agent_type})"
