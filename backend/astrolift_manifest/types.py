"""
Manifest types — the parsed and normalized shapes.

Kept separate from the parser so re-exports are cheap and other
modules can type-hint against ``NormalizedManifest`` without pulling
in the parser logic.
"""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass(slots=True, frozen=True)
class WorkloadManifest:
    name: str
    kind: str
    is_public: bool = False
    schedule: str | None = None
    # CronJob concurrency policy (#427). One of ``forbid`` / ``queue``
    # / ``replace``; only meaningful when ``kind == "cronjob"``. The
    # default ``forbid`` mirrors the manifest renderer's pre-#427
    # hard-coded value, so a manifest that omits the key keeps the
    # existing K8s behaviour.
    concurrency_policy: str = "forbid"
    replicas: int = 1
    cpu_request: str | None = None
    cpu_limit: str | None = None
    memory_request: str | None = None
    memory_limit: str | None = None
    hpa_min: int | None = None
    hpa_max: int | None = None
    hpa_target_cpu_pct: int = 80
    storage_class: str | None = None
    storage_size: str | None = None
    containers: tuple[ContainerManifest, ...] = ()
    # Volume declarations from ``[[workloads.<name>.volumes]]`` (#739).
    # Each dict carries the raw parsed shape (name, kind, mount_path,
    # size, storage_class, access_mode) so the platform can persist them
    # and the workload-detail page can render volume cards without re-
    # parsing the TOML. Kept as dicts (not VolumeDecl) so this module
    # stays independent of security_volumes.py.
    volumes: tuple[dict, ...] = ()
    # Agent dispatch tuning (#795). Only meaningful when
    # ``kind == "agent"`` — the renderer injects ``max_retries`` /
    # ``tool_timeout_seconds`` as the ``ASTROLIFT_MAX_RETRIES`` /
    # ``ASTROLIFT_TOOL_TIMEOUT`` env vars on the agent's primary
    # container so the in-pod agent runtime can read its retry budget
    # and per-tool timeout. ``result_ttl_hours`` governs how long the
    # platform retains an AgentRun's result (#804) and does not affect
    # rendered K8s output. Defaults mirror the manifest spec.
    max_retries: int = 5
    tool_timeout_seconds: int = 300
    result_ttl_hours: int = 72
    # Temporal worker config (#796). Only meaningful when
    # ``kind == "workflow"`` — the renderer stamps a
    # ``astrolift.dev/workload-kind: workflow`` pod annotation and injects
    # ``ASTROLIFT_WORKFLOW_TYPE`` / ``ASTROLIFT_TASK_QUEUE`` /
    # ``TEMPORAL_NAMESPACE`` so the in-pod worker registers the right
    # workflow type and polls the right task queue against the platform
    # Temporal cluster. ``workflow_type`` / ``task_queue`` are required by
    # the parser for workflow workloads; the concurrency caps tune the
    # worker's poller and default to the Temporal SDK's common values.
    workflow_type: str = ""
    task_queue: str = ""
    temporal_namespace: str = "default"
    max_concurrent_activities: int = 20
    max_concurrent_workflows: int = 10

    # ``kind == "function"`` — Knative Serving autoscaling parameters.
    # ``min_scale = 0`` enables scale-to-zero. ``max_scale`` caps the
    # replica count. ``concurrency`` is Knative ``containerConcurrency``.
    # ``function_timeout_seconds`` is Knative ``timeoutSeconds``.
    min_scale: int = 0
    max_scale: int = 10
    function_concurrency: int = 1
    function_timeout_seconds: int = 300

    # kind == "static_site" — built assets served from object storage + CDN,
    # no container/pod. static_build_command non-empty => platform-build mode;
    # empty => CI-pushed mode. static_output_dir is the build artifact dir to
    # sync; static_spa adds the CDN 403/404 -> /index SPA rewrite; static_index
    # is default_root_object.
    static_build_command: str = ""
    static_output_dir: str = ""
    static_spa: bool = False
    static_index: str = "index.html"


@dataclasses.dataclass(slots=True, frozen=True)
class ContainerManifest:
    name: str
    is_primary: bool = False
    image_ref: str | None = None
    dockerfile_path: str = "Dockerfile"
    build_context: str = "."
    port: int = 0
    command: tuple[str, ...] = ()
    args: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    healthcheck_kind: str = "none"
    healthcheck_value: str = ""
    healthcheck_port: int | None = None


@dataclasses.dataclass(slots=True, frozen=True)
class ManagedServiceManifest:
    kind: str
    name: str = ""
    variant: str | None = None
    config: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(slots=True, frozen=True)
class BriefRef:
    """A pointer to a brief folder, declared by the manifest's top-level
    ``brief`` key (spec 38).

    ``path`` is the repo-relative path to the brief's entry ``README.md``
    (e.g. ``"brief/README.md"``). The brief *folder* is that file's parent
    directory; sibling files it references are loaded as context later by
    :func:`astrolift_manifest.brief.load_brief`. Parsing only records the
    pointer — it does not read the repo tree.
    """

    path: str


@dataclasses.dataclass(slots=True, frozen=True)
class SkillRef:
    """A pointer to one skill, declared in the manifest's ``skills`` list
    (spec 38 + spec 39's three-source model).

    Three flavours, discriminated by :attr:`kind` (a ``str`` so the parsed
    shape stays JSON-friendly and frozen-dataclass-cheap — no enum import):

    * ``"local"`` — ``{ name = "relative/path" }`` in the TOML, or a bare
      ``"./path"`` string. ``name`` is the skill name, ``path`` is the
      repo-relative folder (agentskills.io layout) read from the fetched
      agent repo tree at resolution time. ``path`` is non-``None``.
    * ``"catalogue"`` — a bare string with no ``/`` (``"pr-review"`` /
      ``"pr-review@1.2.0"``). Resolved from the built-in catalogue repo.
      ``path`` is ``None``; ``ref`` carries the optional ``@`` pin.
    * ``"org_repo"`` — a string containing a ``/`` (spec 39 §2,
      ``"<alias>/<skill-path>@<ref>"``, e.g. ``"acme/dev-skills/pr-review@v2"``
      → alias ``acme``, subpath ``dev-skills/pr-review``). Resolved from one
      of the org's registered skill repos (``astrolift_agents.OrgSkillRepo``).
      ``repo_alias`` + ``skill_subpath`` are set; ``ref`` is the optional
      ``@`` pin (else the repo's ``default_ref`` at resolution time). ``path``
      is ``None`` (the subpath is repo-relative *within the org repo*, not the
      agent's own repo).

    :attr:`is_local` is derived from ``kind`` (``kind == "local"``) so the
    Phase-3 resolver + registration call sites that branch on ``ref.is_local``
    keep working unchanged.

    Parsing only records the pointer; it does not fetch or load the skill
    folder. The skill folder itself is parsed by
    :func:`astrolift_manifest.skills.load_skill`.
    """

    name: str
    path: str | None = None
    # ``"local"`` | ``"catalogue"`` | ``"org_repo"`` — the source discriminator.
    kind: str = "catalogue"
    # org_repo only: the registered repo alias + the skill folder's path
    # *within that repo* (the part after the first ``/``). ``ref`` is the
    # optional ``@`` pin on catalogue + org_repo refs (a tag/branch/sha);
    # empty string means "no pin — use the catalogue/repo default ref".
    repo_alias: str = ""
    skill_subpath: str = ""
    ref: str = ""

    @property
    def is_local(self) -> bool:
        """``True`` for a local (agent-repo) skill. Derived from :attr:`kind`
        so existing ``ref.is_local`` call sites (skill_resolver,
        agent_skill_registration, manifest_sync) need no change."""
        return self.kind == "local"


@dataclasses.dataclass(slots=True, frozen=True)
class RawManifest:
    name: str
    workloads: tuple[WorkloadManifest, ...] = ()
    managed_services: tuple[ManagedServiceManifest, ...] = ()
    # Agent brief + skills (spec 38). ``brief`` is an optional pointer to a
    # brief folder's entry README; ``skills`` is the ordered list of skill
    # pointers (local-path or named-global). Both stay thin pointers — the
    # substance lives in packaged folders loaded at registration. Empty /
    # ``None`` for non-agent manifests that declare neither.
    brief: BriefRef | None = None
    skills: tuple[SkillRef, ...] = ()
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(slots=True, frozen=True)
class LoadedSkill:
    """A parsed agentskills.io skill folder (spec 38, §Formats).

    Produced by :func:`astrolift_manifest.skills.load_skill` from a skill
    folder's ``SKILL.md`` (YAML frontmatter + markdown body) and the folder
    listing. Maps onto the future ``Skill`` record (name/description/
    instructions) + its bundled ``scripts/`` → ``ToolDef`` rows; ``Skill``
    persistence is Phase 2 and not part of this dataclass.

    * ``name`` / ``description`` — required SKILL.md frontmatter.
    * ``instructions`` — the markdown body after the frontmatter (stripped).
    * ``scripts`` / ``references`` / ``assets`` — repo-relative paths to the
      files under the skill folder's ``scripts/`` / ``references/`` /
      ``assets/`` subdirectories, sorted. Empty when the subdir is absent.
    * ``frontmatter`` — the full parsed frontmatter mapping (so optional
      agentskills.io keys beyond name/description pass through to Phase 2
      without this loader having to enumerate them).
    """

    name: str
    description: str
    instructions: str
    scripts: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    assets: tuple[str, ...] = ()
    frontmatter: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(slots=True, frozen=True)
class LoadedBrief:
    """A parsed brief folder (spec 38, §Formats).

    Produced by :func:`astrolift_manifest.brief.load_brief` from a brief
    folder's entry ``README.md``. Maps onto the future ``Brief`` record;
    ``Brief`` persistence is Phase 2 and not part of this dataclass.

    * ``readme_text`` — the raw README markdown.
    * ``referenced_paths`` — repo-relative paths to sibling files the README
      links to that resolve *within* the brief folder, sorted. These are the
      additional context files loaded for the agent at dispatch. Links that
      escape the folder (``../``), absolute URLs, and anchors are ignored.
    """

    readme_text: str
    referenced_paths: tuple[str, ...] = ()


@dataclasses.dataclass(slots=True, frozen=True)
class NormalizedManifest:
    """Concrete, defaulted shape stored on RegisteredApp.

    Always has every workload + container fully resolved (no "use the
    org default" left over), with a stable key ordering so the SHA-256
    hash is deterministic.
    """

    name: str
    workloads: tuple[WorkloadManifest, ...]
    managed_services: tuple[ManagedServiceManifest, ...]
    defaults_applied: tuple[str, ...]  # which defaults filled in
    serialized: dict[str, Any]  # JSON-shaped for storage
