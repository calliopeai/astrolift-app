"""Strawberry input and payload types for the mutation package."""

from __future__ import annotations

import strawberry

from astrolift_graphql import GUID


@strawberry.input
class RegisterAppInput:
    project_id: GUID
    # Both optional (#friendly-name): when a caller omits them — the wizard's
    # "just register it" path, the CLI, repo-scan bootstrap — the resolver
    # auto-fills a memorable name/slug (e.g. "exciting-talkative-platypus")
    # instead of failing "name is required". A supplied slug is honored as-is
    # and still conflict-checked.
    name: str | None = None
    slug: str | None = None
    description: str | None = None
    source_kind: str = "github"
    source_repo: str
    source_url: str | None = None
    manifest_path: str | None = None
    default_branch: str | None = None
    deploy_branch: str | None = None
    trigger_mode: str | None = None
    # Build configuration. ``build_mode`` selects how the deployable
    # image is produced: ``ci_pushed`` (default — CI publishes and the
    # platform deploys), ``platform_build`` (platform fetches the source
    # tree and invokes a BuildDriver), or ``none`` (a pre-built tag, no
    # build). The ``dockerfile_path`` / ``build_context`` / ``build_args``
    # trio is only meaningful under ``platform_build`` but is accepted in
    # every mode so the wizard can capture it up front. ``build_args`` is
    # a flat string→string map sent as JSON; None falls back to the empty
    # default on the row.
    build_mode: str = "ci_pushed"
    # Orthogonal "how to build" axis (#867). ``build_mode`` decides who
    # publishes the image; ``build_strategy`` selects which builder the
    # platform invokes when it performs the build itself: ``off`` (the
    # default — no strategy), ``dockerfile`` / ``buildpacks`` /
    # ``nixpacks``. Validated against the model choices.
    build_strategy: str = "off"
    dockerfile_path: str = "Dockerfile"
    build_context: str = "."
    build_args: strawberry.scalars.JSON | None = None
    # Five-field cron expression. Required when ``trigger_mode == 'cron'``
    # and ignored otherwise; the resolver enforces both rules.
    cron_expression: str | None = None
    # Approval policy (#291). ``approver_team_id`` is the team GUID;
    # ``approver_user_ids`` are Django user PKs as strings (matching
    # ``AstroliftUser.id`` shape — the User model is the stock Django
    # one with integer PKs). An empty approver set with
    # ``requires_approval=True`` means anyone with the
    # ``app.approve_deploy`` permission on the org may approve.
    requires_approval: bool | None = None
    approver_team_id: GUID | None = None
    approver_user_ids: list[str] | None = None
    minimum_approvals: int | None = None
    # Optional: the wizard may pre-fetch the manifest (via
    # astroliftSourceFile) and pass the body here so the new app
    # boots with manifest_raw already populated. The onboarding
    # workflow still resyncs from the repo's default branch — this is
    # just the seed that lets ManifestParse activities run before the
    # first GitHub/GitLab API call lands.
    manifest_raw: str | None = None


@strawberry.input
class RegisterAgentRepoInput:
    """Register every agent manifest in a repo as an agent Workload (spec 33 PR-3).

    Points the platform at ``source_repo`` (``owner/name``) and registers
    each ``agents/<slug>/astrolift.toml`` (monorepo) plus a root
    ``astrolift.toml`` (single) that declares an agent, as its own agent
    ``Workload`` under its own ``RegisteredApp``. All rows land under
    ``project_id`` (its organization is the tenancy boundary).

    Idempotent on ``(source_repo, manifest_path)``: re-running registers
    only manifests not already registered for the repo, so this same input
    drives both first-time registration and a re-scan that picks up newly
    added agents. ``ref`` is the branch/sha to read the tree at (defaults to
    the repo's default branch handle); ``default_branch`` / ``deploy_branch``
    seed the created apps' branch fields.

    ``manifest_paths`` optionally restricts registration to a subset of the
    discovered manifests (spec 33 PR-8 / #933): when given, only manifests at
    those paths are registered — the wizard's checked agents. Paths that
    aren't among the discovered manifests are ignored. When omitted/empty,
    every discovered agent manifest is registered (unchanged behaviour).
    """

    project_id: GUID
    source_repo: str
    source_kind: str = "github"
    source_url: str | None = None
    ref: str = "main"
    default_branch: str | None = None
    deploy_branch: str | None = None
    manifest_paths: list[str] | None = None


@strawberry.type(name="AstroliftRegisteredAgent")
class RegisteredAgentType:
    """One agent app registered (or matched) by ``registerAgentRepo``.

    ``created`` is False when an app already existed for the repo + manifest
    path (idempotent re-run / re-scan), True when this call created it.
    """

    manifest_path: str
    slug: str
    app_id: GUID
    workload_slug: str
    created: bool
    # Non-fatal brief/skill-resolution warnings for this agent (spec 38
    # Phase 3): a missing local skill path, a name absent from the built-in
    # catalogue, or an unavailable catalogue. The agent still registers when
    # present — empty when everything resolved.
    skill_notes: list[str]


@strawberry.type(name="AstroliftRegisterAgentRepoResult")
class RegisterAgentRepoResultType:
    """Payload of ``registerAgentRepo``.

    ``agents`` lists the per-agent outcome and ``workflows`` lists reconciled
    source-owned workflow slugs. The mutation envelope (ok / error) wraps
    this; either list may be empty for a workflow-only or agent-only repo.
    """

    agents: list[RegisteredAgentType]
    workflows: list[str]


@strawberry.input
class RegisterAppRepoInput:
    """Register every app manifest in a repo as its own app (#979).

    Points the platform at ``source_repo`` (``owner/name``) and registers each
    ``apps/<slug>/astrolift.toml`` (monorepo / multi-service) plus a root
    ``astrolift.toml`` that declares a deployable (non-agent) app, as its own
    ``RegisteredApp``. Each service builds from its own subdir. All rows land
    under ``project_id`` (its organization is the tenancy boundary).

    Idempotent on ``(source_repo, manifest_path)``: re-running registers only
    manifests not already registered for the repo, so the same input drives
    both first-time registration and a re-scan that picks up newly-added
    services. ``ref`` is the branch/sha to read the tree at; ``default_branch``
    / ``deploy_branch`` seed the created apps' branch fields.
    """

    project_id: GUID
    source_repo: str
    source_kind: str = "github"
    source_url: str | None = None
    ref: str = "main"
    default_branch: str | None = None
    deploy_branch: str | None = None


@strawberry.type(name="AstroliftRegisteredAppEntry")
class RegisteredAppEntryType:
    """One app registered (or matched) by ``registerAppRepo``.

    ``created`` is False when an app already existed for the repo + manifest
    path (idempotent re-run / re-scan), True when this call created it.
    ``build_context`` is the per-service subdir the app builds from.
    """

    manifest_path: str
    slug: str
    app_id: GUID
    build_context: str
    created: bool


@strawberry.type(name="AstroliftRegisterAppRepoResult")
class RegisterAppRepoResultType:
    """Payload of ``registerAppRepo``.

    ``apps`` lists the per-manifest outcome (created or matched). The mutation
    envelope (ok / error) wraps this; ``apps`` is empty when the repo carried
    no app manifests.
    """

    apps: list[RegisteredAppEntryType]


@strawberry.input
class UpdateAppInput:
    id: GUID
    name: str | None = None
    description: str | None = None
    source_url: str | None = None
    manifest_path: str | None = None
    default_branch: str | None = None
    deploy_branch: str | None = None
    trigger_mode: str | None = None
    # Build configuration. See ``RegisterAppInput`` for the per-field
    # semantics. On update each is a None-sentinel: a field left None is
    # untouched, so a caller can flip ``build_mode`` to ``platform_build``
    # without having to re-send the Dockerfile path it already saved.
    build_mode: str | None = None
    # See ``RegisterAppInput.build_strategy``. None-sentinel on update:
    # left None it stays as-is.
    build_strategy: str | None = None
    dockerfile_path: str | None = None
    build_context: str | None = None
    build_args: strawberry.scalars.JSON | None = None
    cron_expression: str | None = None
    preview_enabled: bool | None = None
    is_active: bool | None = None
    # Approval policy (#291). See ``RegisterAppInput`` for semantics.
    # ``approver_user_ids`` is treated as a full replacement set when
    # provided (None leaves the existing set untouched).
    requires_approval: bool | None = None
    approver_team_id: GUID | None = None
    approver_user_ids: list[str] | None = None
    minimum_approvals: int | None = None
    # ``cron_paused`` controls scheduled-deploy dispatch without
    # touching ``trigger_mode``. Pausing keeps the cron expression
    # intact so resume re-enables fire-on-schedule immediately.
    cron_paused: bool | None = None
    # Optimistic-concurrency gate (#497). Null = skip the check
    # (back-compat). When present, the resolver compares against
    # ``RegisteredApp.version`` and returns ``VERSION_MISMATCH`` if
    # the persisted row has moved on.
    if_match_version: int | None = None


@strawberry.input
class SetAppSubdomainInput:
    id: GUID
    subdomain: str


@strawberry.input
class SoftDeleteAppInput:
    id: GUID


@strawberry.input
class TearDownAppInput:
    """Two-axis safety for ``tearDownApp`` (#358).

    ``deleteData`` False (default): graceful per-binding deprovision —
    final snapshots taken, retained buckets, etc. The artifacts
    survive for operator-initiated restore.

    ``deleteData`` True: irreversibly delete persistent state on
    every bound managed service.

    ``forceDestroy`` False (default): respect cloud-side deletion
    protection. Bound services with protection on will refuse and
    surface to the operator via the workflow result.

    ``forceDestroy`` True: bypass guards (--atomic cleanup).

    UI surfaces both as separate explicit checkboxes.
    """

    id: GUID
    delete_data: bool = False
    force_destroy: bool = False


@strawberry.type
class _SoftDeletePayload:
    id: GUID
    deleted: bool


@strawberry.input
class UpdateManifestInput:
    """Stage an edit to the source astrolift.toml.

    Writes to ``manifest_raw_staged`` rather than ``manifest_raw`` —
    the editor is a draft buffer until ``pushManifestToRepo`` (which
    opens a PR) or ``syncManifestFromRepo`` (which discards the
    draft) is called.
    """

    id: GUID
    raw_manifest: str


@strawberry.input
class SyncManifestFromRepoInput:
    """Re-fetch ``astrolift.toml`` from the source repo's default
    branch, overwriting both ``manifest_raw`` AND any unsaved
    ``manifest_raw_staged`` draft."""

    id: GUID


@strawberry.input
class ApplyStagedManifestInput:
    """Apply ``manifest_raw_staged`` directly to ``manifest_raw`` (#1759).

    Only for apps that cannot go through ``pushManifestToRepo`` -- no
    ``source_repo``, or no usable ``SourceConnection`` for it. An app that
    can push keeps using the PR flow so a change still goes through review.
    """

    id: GUID


@strawberry.input
class TransferAppInput:
    """Move a registered app to a different team or project.

    Both targets are optional — at least one must be provided. When
    only ``target_team_id`` is given, the app moves under that team
    and re-anchors its project to a project under the new team
    (callers normally pair this with ``target_project_id``).
    Transfers across organizations are NOT permitted: source and
    target must share an org. Use a separate workflow (federation)
    for cross-org moves.
    """

    app_id: GUID
    target_team_id: GUID | None = None
    target_project_id: GUID | None = None


@strawberry.input
class MoveAppToTeamInput:
    """Change the app's primary / home team.

    Pairs with ``transferApp`` for the FK move; the difference here is
    that ``moveAppToTeam`` also keeps the ``AppTeamAccess`` join table
    coherent: the target team gets an active ``OWNER`` row materialized
    if it didn't already have one, and the *previous* home team is
    downgraded to ``DEPLOYER`` so its existing access isn't silently
    revoked. A direct revoke would be the wrong default — the
    operator can call ``revokeTeamAccessFromApp`` explicitly when
    they actually want the old team to lose access. Downgrade-only is
    safer than revoke-on-move and preserves the audit trail.
    """

    app_id: GUID
    target_team_id: GUID


@strawberry.input
class GrantTeamAccessInput:
    """Grant or update a team's access to an app.

    ``accessLevel`` is one of ``viewer`` / ``deployer`` / ``owner``;
    the mutation upserts so re-granting at the same level is
    idempotent and changing the level on an existing row is a
    one-call update.
    """

    app_id: GUID
    team_id: GUID
    access_level: str


@strawberry.input
class RevokeTeamAccessInput:
    app_id: GUID
    team_id: GUID


@strawberry.input
class PushManifestToRepoInput:
    """Open a PR against the source repo with the staged TOML.

    No-op (returns ok + 'nothing_to_push' note) when there's no
    pending staged change."""

    id: GUID
    pr_title: str | None = None
    pr_body: str | None = None
    branch_name: str | None = None


@strawberry.type
class _ManifestStagePayload:
    id: GUID
    sync_state: str
    raw_manifest: str
    raw_manifest_staged: str


@strawberry.type
class _ManifestPushPayload:
    id: GUID
    pr_url: str
    branch_name: str
    note: str


@strawberry.input
class AssignAppToProjectInput:
    """Re-assign an app to a project, or unassign it (#391).

    ``project_guid`` is None to unassign — the app's ``project`` FK
    goes to NULL and the app falls into the team's "Unassigned" nav
    bucket. Otherwise the project is resolved by GUID and must share
    the app's organization. Cross-org assignment is refused.
    """

    app_slug: str
    project_guid: GUID | None = None


@strawberry.input
class PauseAppWebhookDeploysInput:
    """Pause app-global webhook-fired deploys (#399).

    ``reason`` is optional but encouraged — it lands on the audit log
    AND the row itself so the Settings card can render "Paused by X
    · 5m ago — reason: storm" without an audit-log join. Empty / null
    is accepted (the operator may pause without recording context).
    Capped at 512 chars on the model — anything longer is truncated
    at the resolver boundary to keep the audit row reasonable.
    """

    app_slug: str
    reason: str | None = None


@strawberry.input
class ResumeAppWebhookDeploysInput:
    """Lift the app-global webhook-deploy pause (#399).

    No reason field on resume — the audit log captures who flipped it
    back and the timestamp, which is enough provenance for the
    inverse operation. (The original pause's reason stays on the
    audit history; we clear it from the row so a stale string
    doesn't read as the *current* reason on the next pause.)
    """

    app_slug: str


@strawberry.input
class UpdateSecurityPolicyInput:
    """Update the supply-chain / scanner policy for an app (#313).

    Every knob is required on the wire: the form on
    ``/apps/[slug]/security`` always submits the full effective
    policy. ``block_on_high_cve_threshold`` is the one nullable knob —
    None clears the count-based gate; a non-null integer must be at
    least 1 (a threshold of 0 would block every image and is almost
    certainly an input error).
    """

    app_slug: str
    block_on_critical_cves: bool
    block_on_missing_signature: bool
    block_on_high_cve_threshold: int | None


@strawberry.input
class ResyncManifestFromRepoInput:
    """Re-fetch ``astrolift.toml`` from the deploy branch and
    reconcile workloads / env / managed services / schedules.

    Non-destructive on staged drafts: when the DB has an unpushed
    staged manifest AND the repo has new content, the mutation
    refuses (status="diverged") rather than clobbering the draft.
    The destructive equivalent is ``syncManifestFromRepo`` (#277).
    """

    app_slug: str


@strawberry.type
class ResyncManifestPayload:
    """Payload of ``resyncAstroliftManifestFromRepo`` (#386).

    ``sync_state`` is one of ``in_sync``, ``applied``, ``diverged``,
    ``fetch_failed``. ``summary`` is the human-readable one-liner
    the UI surfaces in the success toast. The per-bucket diff
    fields let the FE render a more detailed breakdown when
    ``applied``.
    """

    sync_state: str
    summary: str
    workloads_added: list[str]
    workloads_removed: list[str]
    workloads_changed: list[str]
    managed_services_added: list[str]
    managed_services_removed: list[str]
    env_keys_changed: int
    schedules_changed: int


@strawberry.input
class SetRetentionPolicyInput:
    app_slug: str
    signal: str
    retention_days: int


@strawberry.input
class ArchiveAppInput:
    app_slug: str


@strawberry.input
class RestoreAppInput:
    app_slug: str
