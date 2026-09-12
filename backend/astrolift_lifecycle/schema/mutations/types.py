"""Strawberry input and payload types for the mutation package."""

from __future__ import annotations

import strawberry

from astrolift_graphql import GUID
from astrolift_graphql.errors import MutationErrorType
from astrolift_lifecycle.schema.types import (
    DeploymentType,
    DeployTokenType,
)


@strawberry.input
class StartDeploymentInput:
    app_slug: str
    environment_name: str
    image_tag: str = ""
    source_ref: str | None = None
    image_digest: str | None = None
    workload_slug: str | None = None
    trigger_kind: str = "manual"
    # CI / VCS provenance (#166). Optional at the GraphQL boundary so
    # manual deploys from the UI work without specifying these; the
    # webhook path always populates them.
    ci_actor_kind: str | None = None
    commit_sha: str | None = None
    branch: str | None = None
    ci_run_url: str | None = None
    ci_provider: str | None = None
    # Commit-message provenance (#419) — surfaced on the approval card
    # so approvers can see what they're greenlighting. Optional so the
    # manual-UI path keeps working.
    commit_message: str | None = None
    commit_author: str | None = None
    # GitHub PR + avatar provenance (#722) — populated by the push
    # webhook when the deploy was triggered from a PR; manual UI
    # deploys leave these unset (pr_number defaults to 0, FE renders
    # a dash).
    pr_number: int | None = None
    commit_author_avatar_url: str | None = None
    # Rollout strategy (#736).  The Temporal workflow's rollout-policy
    # step decides this; manual deploys default to "rolling" (the
    # safest k8s default).  Accepted values mirror Deployment.Strategy.
    strategy: str | None = None


@strawberry.input
class PromoteDeploymentInput:
    app_slug: str
    source_environment_name: str
    target_environment_name: str


@strawberry.input
class DeploymentByIdInput:
    id: GUID


@strawberry.input
class AbortDeploymentInput:
    """``reason`` is required + non-empty (#419) so every rejection
    leaves a paper trail. Validated at the resolver boundary; we
    persist it on the row + the audit-log extras payload so the
    history sidebar can render it without an extra join."""

    id: GUID
    reason: str


@strawberry.input
class ApproveByTokenInput:
    """Public mutation input — the token is the auth proof.

    No tenant context, no permission check: the SHA-256 of ``token``
    must match an active, unconsumed, unexpired
    ``Deployment.approval_token_hash``."""

    token: str


@strawberry.input
class RejectByTokenInput:
    """Public mutation input — same auth model as ``ApproveByTokenInput``.

    ``reason`` is recorded on the deployment's lifecycle event for the
    audit trail."""

    token: str
    reason: str | None = None


@strawberry.input
class BulkApproveDeploymentsInput:
    """Bulk-approve N pending deploys in one call (#420).

    ``deployment_ids`` is capped at 50 — defensive ceiling so a runaway
    selection doesn't fan into a single huge audit-write burst.
    ``reason`` is optional and carried onto the audit-extras payload
    for every approved id."""

    deployment_ids: list[GUID]
    reason: str | None = None


@strawberry.input
class BulkRejectDeploymentsInput:
    """Bulk-reject N pending deploys in one call (#420).

    Same shape as :class:`BulkApproveDeploymentsInput` but ``reason``
    is required + non-empty (mirrors the single-id reject contract)
    so every rejected deploy carries an auditable explanation."""

    deployment_ids: list[GUID]
    reason: str


@strawberry.type(name="AstroliftBulkDeploymentResultItem")
class BulkDeploymentResultItem:
    """Per-id outcome from a bulk approve / reject pass.

    ``ok`` is True when the per-id resolver succeeded; ``deployment``
    carries the post-mutation row in that case. On failure ``errors``
    is non-empty and ``deployment`` is null — the FE renders the
    failure inline alongside the still-pending row so the operator can
    retry just the failing entries."""

    deployment_id: GUID
    ok: bool
    errors: list[MutationErrorType]
    deployment: DeploymentType | None


@strawberry.type(name="AstroliftBulkDeploymentResultData")
class BulkDeploymentResultData:
    """Container for the per-id breakdown returned in
    :class:`AstroliftBulkDeploymentMutationResult.data`. Wrapping the
    list in a dedicated type keeps the standard ``MutationResult``
    envelope shape so callers reuse their existing ``ok / errors /
    data`` handling pattern."""

    results: list[BulkDeploymentResultItem]
    succeeded_count: int
    failed_count: int


@strawberry.input
class TearDownPreviewInputGql:
    id: GUID


@strawberry.input
class ExtendPreviewTtlInputGql:
    """Push a preview's auto-teardown out by N days (#431).

    Allowed ``days`` values are locked to ``{1, 7, 30}`` — the resolver
    rejects anything else. The model layer enforces the +30d ceiling
    from now so an extension on a preview that's already 25 days in
    can't sneak past the cap.
    """

    id: GUID
    days: int


@strawberry.input
class SetPreviewPinnedInput:
    """Set or clear a preview's operator pin (#1399).

    One setter rather than a ``pin`` / ``unpin`` pair so the CLI's two
    verbs and a UI toggle drive the same resolver, and so the operation
    is idempotent: re-sending the state you already have is a no-op
    write, not an error.

    A pin exempts the preview from *both* GC rules (TTL expiry and
    max-active eviction), which is what distinguishes it from
    ``extendPreviewTtl`` — that only moves ``ttl_until``, in capped
    1/7/30-day steps, on the TTL axis alone, with no inverse.

    ``reason`` is free text recorded on the row and truncated to 512
    chars. It is ignored when ``pinned`` is false: unpinning clears the
    whole audit trail, so there is nothing for a reason to annotate.
    """

    id: GUID
    pinned: bool
    reason: str | None = None


@strawberry.input
class CreatePreviewEnvironmentInput:
    """Manually spin up a preview from a branch — no PR required (#751).

    The auto path (PR-opened webhook → BuildPreviewWorkflow) keys on
    ``(registered_app, pr_number)``; this manual path keys on
    ``(registered_app, branch)`` so re-running the mutation on the same
    branch returns the existing preview rather than racing two
    namespaces in.

    ``environment_name`` is the AppEnvironment name carved out for the
    preview deploy. Defaults to ``preview-<branch-slug>`` when the
    caller doesn't pin a value; explicit names let operators run
    parallel previews with distinct deploy_config overrides on the same
    branch (e.g., a perf-tuned variant vs. baseline).
    """

    app_slug: str
    branch: str
    environment_name: str = ""


@strawberry.input
class MigrateAppInputGql:
    app_environment_id: GUID
    target_cluster_id: GUID
    drain_source: bool = True


@strawberry.input
class EnvironmentByIdInput:
    id: GUID


@strawberry.input
class AddAppDomainInput:
    app_slug: str
    hostname: str
    validation_method: str | None = None
    """dns_txt | http_01 | dns_01 (default: dns_txt)"""


@strawberry.input
class AddWildcardDomainInput:
    """Add a ``*.hostname`` wildcard domain (#753).

    ``hostname`` is the apex (e.g. ``example.com``); the platform
    issues a cert covering both the apex and ``*.<hostname>`` SANs.
    Wildcard issuance requires DNS-01 by CA policy — HTTP-01 / DNS-TXT
    can't authorize wildcards — so ``validation_method`` is restricted
    to ``dns_01`` (defaulted; rejected at the resolver if overridden
    to anything else).

    ``sni_cert_ref`` lets the operator pin a provider-specific cert
    identifier for SNI on this hostname. Empty defaults to the
    renderer's auto-pick; populated for multi-cert SNI scenarios
    (e.g., EV on apex + wildcard on subdomains).
    """

    app_slug: str
    hostname: str
    validation_method: str = "dns_01"
    sni_cert_ref: str = ""


@strawberry.input
class RemoveAppDomainInput:
    id: GUID


@strawberry.input
class RecheckDomainValidationInput:
    id: GUID


@strawberry.input
class UploadCustomDomainCertificateInput:
    """BYO-cert payload — operator pastes (or pipes via CLI) the full
    PEM chain + private key. Used when the platform can't auto-issue
    a cert: externally-managed AWS zones (ACM can't HTTP-01), zones
    that hit Let's Encrypt rate limits, or air-gapped / custom-CA
    deployments. The renderer reads the stored PEM verbatim — the
    platform never re-issues a BYO cert."""

    id: GUID
    certificate_pem: str
    private_key_pem: str


@strawberry.type
class _AppDomainRemovedPayload:
    id: GUID
    deleted: bool


@strawberry.input
class DomainRedirectRuleInput:
    """One redirect rule on the replace-all ``setDomainRedirects``
    payload (#742). Mirrors ``DomainRedirectRule``'s persisted shape;
    defaults match the model defaults so the FE only needs to specify
    ``kind`` for the well-known kinds (http_to_https / apex_to_www /
    www_to_apex)."""

    kind: str
    source_pattern: str = ""
    destination_url: str = ""
    http_status: int = 301
    preserve_query_string: bool = True
    priority: int = 0


@strawberry.input
class SetDomainRedirectsInput:
    domain_id: GUID
    rules: list[DomainRedirectRuleInput]


@strawberry.input
class DomainPathRouteInput:
    """One path-prefix routing rule on the replace-all
    ``setDomainPathRoutes`` payload (#740)."""

    path_prefix: str
    target_workload_slug: str
    target_port: int
    strip_prefix: bool = False
    priority: int = 0


@strawberry.input
class SetDomainPathRoutesInput:
    domain_id: GUID
    routes: list[DomainPathRouteInput]


@strawberry.input
class CreateDeployTokenInput:
    app_slug: str
    name: str
    scopes: list[str] | None = None
    expires_at_iso: str | None = None
    """ISO-8601; if absent the token defaults to the platform's
    1-year TTL."""


@strawberry.input
class RotateDeployTokenInput:
    id: GUID


@strawberry.input
class RevokeDeployTokenInput:
    id: GUID


@strawberry.type
class DeployTokenSecretReveal:
    """Returned exactly once on creation/rotation; the plaintext
    token never lives in DB.

    ``rotation_grace_seconds`` is the live Constance-tunable grace
    window (``DEPLOY_TOKEN_ROTATION_GRACE_SECONDS``, default 24h)
    the previous secret stays valid for after a rotate. ``0`` on
    creation (no previous secret to honour). Surfaced so the rotate
    ConfirmDialog can display the *actual* grace operators are
    committing to instead of a hard-coded number that may have
    drifted (#425).
    """

    token: DeployTokenType
    plaintext_secret: str
    rotation_grace_seconds: int = 0


@strawberry.type
class _DeployTokenRevokedPayload:
    id: GUID
    revoked: bool


@strawberry.input
class DeleteAppDnsRecordInput:
    """Drop one DNS record on the app's bound cluster's DnsDriver.

    ``hostname`` is the full FQDN (``api.acme.com``) — the activity
    splits it into ``(name, parent zone)`` and dispatches to the
    DnsDriver. ``recordType`` defaults to CNAME (the most common
    record the platform writes for an app)."""

    app_id: GUID
    hostname: str
    record_type: str = "CNAME"


@strawberry.input
class RevokeAppCertificateInput:
    """Revoke the auto-issued cert tied to a CustomDomain row.

    Resets ``certificate_state`` back to NOT_REQUESTED so the renderer
    stops emitting the Ingress until the operator re-issues or BYO."""

    custom_domain_id: GUID


@strawberry.input
class DeleteAppIdentityRoleInput:
    """Delete the app's cloud IAM/identity role (IRSA / GKE Workload
    Identity / AKS federated cred / projected SA). Subsequent deploys
    re-provision the role on next ``provision_namespace``."""

    app_id: GUID


@strawberry.input
class ArchiveAppRegistryRepoInput:
    """Archive the app's image repo + clear the platform's stored URI.

    The driver's ``archive`` flag controls soft-vs-hard delete on the
    registry side (ECR archives by default). Clearing the URI lets a
    future ``provision_registry_repo`` start cleanly."""

    app_id: GUID
    archive: bool = True


@strawberry.input
class DeleteAppIngressInput:
    """Delete one Ingress (when ``hostname`` given) or every Ingress
    on the app's namespace (when None).

    Per-hostname soft-deletes the matching ``CustomDomain`` row — the
    renderer drops its Ingress on the next deploy. The all-ingresses
    path calls the IngressDriver directly."""

    app_id: GUID
    hostname: str | None = None


@strawberry.type(name="AstroliftCapabilityDeprovisionPayload")
class _CapabilityDeprovisionPayload:
    """Generic envelope for capability-deprovision mutations.

    Carries the cluster slug + a free-form ``detail`` string the UI
    can render. Per-mutation extras (deleted hostnames, role name,
    etc.) live in ``detail`` rather than typed fields — the operator-
    facing display is the same shape across capabilities."""

    app_id: GUID | None
    cluster_slug: str
    detail: str


@strawberry.input
class TriggerDeployWorkflowInput:
    """Input for the rebuild-and-deploy workflow-dispatch mutation (#387).

    ``app_slug`` resolves the RegisteredApp; ``branch`` defaults to
    the app's ``deploy_branch`` (with a ``main`` fallback) when
    None/empty. We accept a slug rather than a guid here for parity
    with ``start_deployment`` — operators tend to script against
    slugs."""

    app_slug: str
    branch: str | None = None


@strawberry.type(name="AstroliftTriggerDeployWorkflowPayload")
class TriggerDeployWorkflowPayload:
    """What the FE renders on a successful workflow dispatch (#387).

    ``run_url`` is the host's runs-page URL for the workflow file —
    GitHub's dispatch endpoint doesn't return a run id, so we link
    to the runs page and the operator watches the new run materialize
    at the top. ``dispatched_branch`` is the branch the dispatch
    actually targeted, after defaulting/normalizing the input."""

    run_url: str
    dispatched_branch: str


@strawberry.input
class SetEnvironmentSettingInput:
    environment_id: GUID
    key: str
    value: str


@strawberry.input
class ClearEnvironmentSettingInput:
    environment_id: GUID
    key: str


@strawberry.input
class ForceRedeployInput:
    """Input for the force-redeploy recovery mutation (#389).

    ``app_slug`` resolves the RegisteredApp; ``environment_name``
    scopes the recovery to one env (None → every env on the app).
    ``confirm_slug`` must equal ``app_slug`` — muscle-memory guard
    against accidental destructive clicks. The FE confirmation modal
    binds the typed-slug field to this argument."""

    app_slug: str
    environment_name: str | None = None
    confirm_slug: str


@strawberry.type(name="AstroliftForceRedeployPayload")
class ForceRedeployPayload:
    """Read-back for a force-redeploy attempt (#389).

    ``deployments_cancelled`` is the count of ``Deployment`` rows
    transitioned out of an in-flight status (PENDING_APPROVAL /
    PENDING / DEPLOYING / REDEPLOYING) into FAILED.

    ``k8s_objects_deleted`` is the count of stub manifests the cluster
    driver accepted on the delete pass. The driver treats not-found
    as ok per the SDK contract — this is the count of stubs that did
    not surface an error, not necessarily the count of objects that
    actually existed.

    ``workflow_dispatched`` is True when the CI workflow-dispatch call
    succeeded. ``run_url`` is the host's runs-page URL when present;
    ``dispatch_message`` carries the failure message when False so
    the FE toast surfaces the partial-success shape (cancellation +
    delete counts are still applied)."""

    deployments_cancelled: int
    k8s_objects_deleted: int
    workflow_dispatched: bool
    run_url: str | None
    dispatch_message: str | None


@strawberry.input
class RestartWorkloadInput:
    workload_id: GUID


@strawberry.input
class ScaleWorkloadInput:
    workload_id: GUID
    replicas: int


@strawberry.type(name="AstroliftWorkloadOpPayload")
class _WorkloadOpPayload:
    """Read-back payload for live workload ops.

    ``new_revision`` is the post-restart ``observedGeneration`` (may
    be None when the driver doesn't surface status). ``desired_replicas``
    + ``ready_replicas`` are the post-scale ``spec.replicas`` /
    ``status.readyReplicas`` read-back. Fields irrelevant to the
    specific op are left None — the UI uses the mutation it called
    to decide which fields to render.
    """

    workload_id: GUID
    new_revision: int | None
    desired_replicas: int | None
    ready_replicas: int | None


@strawberry.input
class PushCiSecretsToRepoInput:
    """Push the five Astrolift CI secrets to ``app_slug``'s source repo.

    Slug rather than guid so the FE button can call the mutation
    without a separate lookup; matches the contract on every other
    CI-side mutation (#384, #387)."""

    app_slug: str


@strawberry.type(name="AstroliftPushCiSecretsPayload")
class PushCiSecretsPayload:
    """Read-back for a successful CI-secrets push (#383).

    ``secret_names`` is the canonical list of names PUT (rendered
    verbatim in the success toast so the operator knows what just
    landed on the repo). ``rotated_token_last_4`` is the last four
    chars of the freshly-minted deploy-token plaintext — the only
    piece of the new token that ever surfaces to the browser; the
    full plaintext is sealed and handed to GitHub. ``repo`` is the
    ``owner/name`` of the target repo, for the toast's repo-label."""

    secret_names: list[str]
    rotated_token_last_4: str
    repo: str


@strawberry.input
class InstallSourceWebhookInput:
    """Install or refresh the push-event webhook on ``app_slug``'s
    source repo. Slug rather than guid so the FE button calls the
    mutation without an extra lookup — same shape every other CI-side
    mutation uses."""

    app_slug: str


@strawberry.type(name="AstroliftInstallSourceWebhookPayload")
class InstallSourceWebhookPayload:
    """Read-back for a successful webhook install / refresh (#385).

    ``status`` is one of:
    * ``created`` — the host registered a brand-new hook.
    * ``refreshed`` — a hook with the same URL already existed on the
      host; we rotated the shared HMAC secret and advanced the
      ``installed_at`` timestamp so the operator's click is observable.
    * ``app_delivers`` — the connection is a github_app_install and the
      org App is installed on the repo; its own webhook already delivers
      pushes. There is no per-repo hook, so ``hook_id`` is empty by
      design — the CLI keys off ``status`` to report this distinctly
      from a real hook. (``not_installed`` never reaches this payload;
      it surfaces as a PRECONDITION failure.)

    ``hook_id`` is the host-side identifier (numeric on GitHub,
    stored as string for GitLab / Bitbucket forward-compat). Empty for
    ``app_delivers`` — the App's own webhook routes deliveries and no
    per-repo hook is created; never treat an empty ``hook_id`` as a
    real hook.

    ``receiver_url`` is the canonical platform URL the host POSTs
    deliveries to — surfaced in the toast so the operator can paste
    it elsewhere if a manual install is ever needed."""

    status: str
    hook_id: str
    receiver_url: str


@strawberry.input
class PushCiWorkflowToRepoInput:
    """Push the rendered Astrolift CI workflow to ``app_slug``'s repo.

    Slug rather than guid so the FE button can call the mutation
    without a separate lookup — matches the contract on the other
    CI-side mutations (#383, #387)."""

    app_slug: str


@strawberry.type(name="AstroliftPushCiWorkflowPayload")
class PushCiWorkflowPayload:
    """Read-back for a successful CI-workflow sync (#384).

    ``status`` discriminates how the change landed:

    * ``created``   — file was missing on the deploy branch; we wrote
                       it. ``commit_sha`` is the commit SHA.
    * ``updated``   — file existed and diverged; we overwrote it.
                       ``commit_sha`` is the new commit SHA.
    * ``in_sync``   — file already matched the rendered template; we
                       did nothing. Both ``commit_sha`` and ``pr_url``
                       are null.
    * ``pr_opened`` — the deploy branch is protected; we landed the
                       file on a side branch and opened a PR back into
                       the deploy branch. ``pr_url`` is the PR URL.

    ``commit_sha`` is null on ``in_sync`` and ``pr_opened`` paths;
    ``pr_url`` is null on ``created`` / ``updated`` / ``in_sync``."""

    status: str
    commit_sha: str | None
    pr_url: str | None


@strawberry.input
class RetryAstroliftAutowireInput:
    """Re-run the autowire chain for ``app_slug`` (#1108).

    Slug rather than guid so the detail-page "Retry autowire" button calls
    the mutation without an extra lookup — same shape as the per-step CI
    mutations it supersedes."""

    app_slug: str


@strawberry.type(name="AstroliftRetryAutowirePayload")
class RetryAstroliftAutowirePayload:
    """Read-back for a retried autowire run (#1108).

    ``connected`` is False for the "no org connection — connect for
    auto-deploy" state (no steps ran). Otherwise each of ``ci_workflow`` /
    ``webhook`` / ``secrets`` is ``ok`` or ``error``; ``detail`` concatenates
    any step error messages so the toast can show what still needs fixing.
    ``all_ok`` is the single boolean the button uses to flip the banner."""

    connected: bool
    all_ok: bool
    ci_workflow: str
    webhook: str
    secrets: str
    detail: str


@strawberry.input
class RunJobOnceInput:
    """Input for the manual one-shot run of a manifest-declared CronJob
    (#390).

    ``app_slug`` resolves the RegisteredApp; ``environment_name`` picks
    the target env (which carries the tenant cluster the Job lands on);
    ``job_slug`` is the cronjob workload's slug from
    ``astrolift.toml`` — slug rather than guid so the FE button calls
    the mutation without an extra lookup."""

    app_slug: str
    environment_name: str
    job_slug: str


@strawberry.type(name="AstroliftRunJobOncePayload")
class RunJobOncePayload:
    """Read-back for a successful manual job-run dispatch (#390).

    ``run_name`` is the freshly-applied k8s Job name
    (``<job_slug>-manual-<8hex>``); operators paste it verbatim into
    ``kubectl logs job/<name>`` when diagnosing outside the UI.
    ``namespace`` is the per-app namespace the Job landed in.
    ``logs_url`` points at the existing scheduled-job-runs surface
    where the run materializes once the cluster reports it."""

    run_name: str
    namespace: str
    logs_url: str | None


@strawberry.input
class DeregisterAppInput:
    """Input for the hard-deregister + full teardown mutation (#392).

    ``app_slug`` resolves the RegisteredApp; ``confirm_name`` must
    equal the app's ``name`` verbatim — a muscle-memory guard against
    accidental destructive clicks. The FE confirmation modal binds
    the typed-name field to this argument so the destructive button
    stays disabled until the operator types the exact name.

    Keep-vs-wipe is an explicit operator choice, defaulting to the SAFE
    corner (#1000): with both flags False (the default) the child
    managed-service deprovisions take an RDS final snapshot and retain
    S3 contents — recoverable. ``delete_data=True`` + ``force_destroy=True``
    is the danger-zone four-corner that irreversibly wipes persistent
    state. ``force_destroy`` without ``delete_data`` is a no-op per the
    driver matrix."""

    app_slug: str
    confirm_name: str
    delete_data: bool = False
    force_destroy: bool = False


@strawberry.type(name="AstroliftDeregisterAppPayload")
class DeregisterAppPayload:
    """Read-back for a deregister kickoff or resume (#392).

    ``workflow_id`` is the deterministic Temporal id
    (``DeregisterAppWorkflow-<app-guid>``) the mutation fired. Re-
    firing the mutation joins the existing run via Temporal de-dup
    on this id so partial-failure resume is a one-click retry.

    ``still_live_resources`` is empty on the initial kickoff (the
    workflow runs async, so the mutation returns before any
    teardown step has completed). The list is populated when an
    operator polls the workflow result on a resume path — the
    workflow's ``WorkflowResult.data["still_live_resources"]``
    carries the resource keys that failed on the prior pass."""

    workflow_id: str
    still_live_resources: list[str]


@strawberry.input
class CancelDeregisterInput:
    """Input for the grace-period cancel mutation (#436 B).

    ``workflow_id`` is the deterministic id the deregister mutation
    returned. ``reason`` is a free-form audit note (the operator's
    why-cancelled context) — surfaces in the workflow log + the
    mutation_audit event extras."""

    workflow_id: str
    reason: str | None = None


@strawberry.type(name="AstroliftCancelDeregisterPayload")
class CancelDeregisterPayload:
    """Read-back for a cancel-deregister attempt (#436 B).

    ``signal_delivered`` is False when the Temporal client couldn't
    reach the workflow (workflow already finished, Temporal disabled
    in dev, transport error). The FE renders the "couldn't reach"
    copy in that case so the operator knows whether their cancel
    landed."""

    workflow_id: str
    signal_delivered: bool


@strawberry.input
class ValidateAstroliftCiSecretsInput:
    """Probe the app's source repo's Actions secrets and report which of
    the five canonical Astrolift names are set (#693).

    Slug rather than guid for parity with ``pushAstroliftCiSecretsToRepo``."""

    app_slug: str


@strawberry.type(name="AstroliftCiSecretValidation")
class CiSecretValidationType:
    """One row of the validate-CI-secrets report (#693).

    The FE renders one chip per row — green check on ``isSet=True &&
    isCurrent in (True, None)``, amber on ``isSet=True && isCurrent=False``
    (set but stale), red X on ``isSet=False``.

    ``updatedAt`` is the GitHub-provided ISO-8601 timestamp; the FE
    formats it as 'updated 2 days ago' next to the chip."""

    secret_name: str
    is_set: bool
    is_current: bool | None
    """None means 'unknown' — the platform has either never pushed
    secrets to this repo (no baseline to compare) or GitHub returned
    no usable updated_at on the row.  FE renders these as a green
    check with no freshness annotation."""

    updated_at: str


@strawberry.type(name="AstroliftValidateCiSecretsPayload")
class ValidateAstroliftCiSecretsPayload:
    """Read-back for ``validateAstroliftCiSecrets`` (#693).

    ``repo`` is the ``owner/name`` of the target repo, mirroring the
    push payload for the FE's repo-label affordance."""

    repo: str
    results: list[CiSecretValidationType]


@strawberry.input
class RunTaskInput:
    """Input for the ``runTask`` mutation (#801).

    ``app_slug`` + ``workload_slug`` identify the ``kind: task``
    workload to execute.  ``environment_name`` picks the target
    cluster; when omitted the app's default environment is used.
    ``command`` overrides the container command so the same workload
    can run one-off variations (e.g. ``["python", "manage.py",
    "migrate", "--database", "secondary"]``) without a manifest edit.
    Pass an empty list (the default) to run the declared command as-is.
    """

    app_slug: str
    workload_slug: str
    environment_name: str | None = None
    command: list[str] = strawberry.field(default_factory=list)


@strawberry.input
class ReapCloudOrphanInput:
    """Input for ``reapCloudOrphan`` (#995).

    ``kind`` + ``reap_key`` come straight off a ``scanCloudOrphans`` result
    row (``CloudOrphanType.kind`` / ``.reap_key``). ``cluster_slug`` is the
    optional hint of which managed cluster's driver should reap a
    cloud-enumerated resource (IAM role); the reaper falls back to any
    capable managed cluster when omitted. ``force_destroy`` removes
    deletion-protection where a resource has it.
    """

    kind: str
    reap_key: str
    cluster_slug: str | None = None
    force_destroy: bool = False


@strawberry.type
class ReapCloudOrphanPayload:
    """Read-back for a reaped orphan (#995).

    ``reaped`` is True when the driver actually deprovisioned the resource;
    ``already_gone`` is True when the resource was found to be already absent
    (an idempotent no-op) — both are successful outcomes.
    """

    kind: str
    identifier: str
    reaped: bool
    already_gone: bool
    message: str


@strawberry.input
class RerunOnboardingInput:
    """Input for the onboarding re-run recovery mutation (#1550).

    ``app_slug`` resolves the RegisteredApp. No confirm field: re-running
    onboarding provisions what is missing and touches nothing that is
    already there, so unlike force-redeploy it cannot drop live traffic."""

    app_slug: str


@strawberry.type(name="AstroliftRerunOnboardingPayload")
class RerunOnboardingPayload:
    """Read-back for an onboarding re-run (#1550).

    ``started`` is false without being an error in the two cases the
    operator most needs told apart, and ``detail`` says which:
    onboarding is already running (so a second run was correctly
    refused), or Temporal is disabled in this environment (so nothing
    was enqueued and nothing will happen).

    ``workflow_id`` is the id the run was submitted under, so an
    operator can find it in Temporal without guessing the format."""

    started: bool
    already_running: bool
    workflow_id: str
    detail: str
