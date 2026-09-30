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
    # Who owns the recipe (#1866): a project, else a team. Neither means the
    # spec is org-shared, explicitly: every spec reader in the org sees it and
    # every team's agents may run with it. The project wins when both are set,
    # and a deleted owner leaves the spec to org-level grants only. RESTRICT,
    # not SET_NULL, because clearing the owner would share a team's secret
    # packet with the whole org.
    team = models.ForeignKey(
        "astrolift_identity.Team",
        related_name="agent_environment_specs",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
    )
    project = models.ForeignKey(
        "astrolift_identity.Project",
        related_name="agent_environment_specs",
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
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
    # Managed model: when on, the task pod uses the cluster's cloud-native
    # model provider (AWS→Bedrock, GCP→Vertex) via a workload-identity
    # ServiceAccount instead of an ANTHROPIC_API_KEY. The dispatcher mints
    # the cloud identity + injects the provider's model env at spawn time.
    managed_model = models.BooleanField(default=False)
    # Run task and box pods as the images' non-root ``agent`` user with all
    # capabilities dropped (#1855). Excludes ``allow_install``.
    run_as_non_root = models.BooleanField(default=False)
    # Boot the agent's payload in a box before its session starts, with the
    # runner's workspace setup ([workspace] repos, deps, MCP) (#1877).
    box_workspace = models.BooleanField(default=False)
    # Send model traffic through the Zentinelle gateway in the pod's cluster
    # with a per-run agent key; no provider key reaches the pod (#1851).
    # Excludes ``managed_model``: the gateway does not proxy Bedrock or Vertex.
    model_gateway = models.BooleanField(default=False)
    # GPUs for task and box pods (#2039), with the same meaning as a
    # manifest workload's: whole GPUs, or MIG slices when mig_profile is set.
    gpu = models.PositiveSmallIntegerField(default=0)
    gpu_type = models.CharField(max_length=63, blank=True, default="")
    mig_profile = models.CharField(max_length=32, blank=True, default="")
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
