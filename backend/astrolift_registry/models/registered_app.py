"""
RegisteredApp — the deployable thing.

Represents a Git repository registered for deployment, plus the
parsed manifest, registry coordinates, and tenant defaults. The
provisioning state machine lives on the model itself; mutating the
state field directly is forbidden — call ``transition_to``.

See ``specs/04`` §6.1.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from core.models.base import NamedBaseCoreModel


class RegisteredApp(NamedBaseCoreModel):
    class SourceKind(models.TextChoices):
        GITHUB = "github"
        GITLAB = "gitlab"
        BITBUCKET = "bitbucket"
        GITEA = "gitea"
        GIT_URL = "git_url"
        # Apps promoted from a Calliope App Builder DevEnvironment
        # (#767, #768). No source repo; the canonical artifact is the
        # file tree captured on the DevEnvironment at promote time and
        # baked into a container image by the platform.
        DIRECT_UPLOAD = "direct_upload"

    class TriggerMode(models.TextChoices):
        AUTO_ON_PUSH = "auto_on_push"
        MANUAL = "manual"
        EXTERNAL_CI = "external_ci"
        CRON = "cron"

    class BuildMode(models.TextChoices):
        # CI builds and pushes the image; the platform only deploys the
        # already-published tag. The historical behaviour and the
        # default for every app registered before this field existed.
        CI_PUSHED = "ci_pushed"
        # The platform fetches the source tree and invokes a BuildDriver
        # to build + push the image itself (no CI integration required).
        PLATFORM_BUILD = "platform_build"
        # The image tag is supplied directly and no build step runs at
        # all — the operator points the app at a pre-built image.
        NONE = "none"

    class BuildStrategy(models.TextChoices):
        # *How* the platform builds the image, orthogonal to ``BuildMode``
        # (which decides *who* publishes it — #867). Consumed by
        # ``BuildImageActivity`` only when the platform performs the build.
        # ``off`` (default) means no platform build strategy is selected;
        # ``dockerfile`` builds from the repo's Dockerfile, while
        # ``buildpacks`` / ``nixpacks`` use the respective toolchains
        # without a Dockerfile.
        OFF = "off"
        DOCKERFILE = "dockerfile"
        BUILDPACKS = "buildpacks"
        NIXPACKS = "nixpacks"

    class ProvisioningStatus(models.TextChoices):
        PENDING = "pending"
        PROVISIONING = "provisioning"
        READY = "ready"
        FAILED = "failed"
        # Teardown lifecycle (#993): the deregister workflow flips an app to
        # TEARING_DOWN while it deprovisions, then DEREGISTERED once cleanup
        # completes. Both values are written by app_teardown's mark_* activities
        # via ``transition_provisioning(ProvisioningStatus(...))`` — they MUST be
        # enum members or that coercion raises ValueError and teardown crashes
        # (leaving orphaned cloud resources, never converging).
        TEARING_DOWN = "tearing_down"
        DEREGISTERED = "deregistered"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="registered_apps",
        on_delete=models.CASCADE,
    )
    team = models.ForeignKey(
        "astrolift_identity.Team",
        related_name="registered_apps",
        on_delete=models.CASCADE,
    )
    # Nullable so the operator can unassign an app from its project
    # (and so the nav-tree code can render "Unassigned" buckets at the
    # team / org levels — #391). When the project itself is hard-deleted
    # we clear the FK rather than cascading the destruction; soft-deletes
    # leave the FK in place because the project row stays in the table.
    project = models.ForeignKey(
        "astrolift_identity.Project",
        related_name="registered_apps",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    source_kind = models.CharField(max_length=32, choices=SourceKind.choices, default=SourceKind.GITHUB)
    source_repo = models.CharField(max_length=255, blank=True, default="")
    source_url = models.URLField(blank=True, default="")
    manifest_path = models.CharField(max_length=255, default="astrolift.toml")
    default_branch = models.CharField(max_length=128, default="main")

    # How the deployable image is produced for this app. ``ci_pushed``
    # (default) keeps the historical contract where CI publishes the
    # image and the platform only rolls it out. ``platform_build`` hands
    # the source tree to a BuildDriver; ``none`` skips building entirely
    # and deploys a tag supplied directly. ``dockerfile_path`` /
    # ``build_context`` / ``build_args`` are consumed only under
    # ``platform_build`` (they're ignored for the other two modes but
    # captured at registration so the contract is stable when the
    # platform-build workflow lands).
    build_mode = models.CharField(
        max_length=32,
        choices=BuildMode.choices,
        default=BuildMode.CI_PUSHED,
    )
    # Orthogonal "how to build" axis (#867). ``build_mode`` decides who
    # publishes the image; ``build_strategy`` decides which builder the
    # platform invokes when it performs the build itself. ``off`` (the
    # default) selects no strategy; ``dockerfile`` / ``buildpacks`` /
    # ``nixpacks`` pick the toolchain. Read by ``BuildImageActivity``.
    build_strategy = models.CharField(
        max_length=32,
        choices=BuildStrategy.choices,
        default=BuildStrategy.OFF,
    )
    dockerfile_path = models.CharField(max_length=512, default="Dockerfile", blank=True)
    build_context = models.CharField(max_length=512, default=".", blank=True)
    build_args = models.JSONField(default=dict, blank=True)

    manifest_raw = models.TextField(blank=True, default="")
    manifest_raw_staged = models.TextField(blank=True, default="")
    manifest_normalized = models.JSONField(default=dict, blank=True)
    manifest_hash = models.CharField(max_length=128, blank=True, default="")
    last_synced_hash = models.CharField(max_length=128, blank=True, default="")

    registry_repo_uri = models.CharField(max_length=512, blank=True, default="")
    registry_pull_secret_ref = models.CharField(max_length=512, blank=True, default="")
    push_role_ref = models.CharField(max_length=512, blank=True, default="")

    k8s_namespace = models.CharField(max_length=128, blank=True, default="")
    subdomain = models.CharField(max_length=128, blank=True, default="")
    is_active = models.BooleanField(default=True)
    provisioning_status = models.CharField(
        max_length=32,
        choices=ProvisioningStatus.choices,
        default=ProvisioningStatus.PENDING,
    )
    provisioning_error = models.TextField(blank=True, default="")
    deploy_token_hash = models.CharField(max_length=128, blank=True, default="")
    deploy_token_last_4 = models.CharField(max_length=4, blank=True, default="")
    log_retention_days = models.PositiveIntegerField(default=30)
    preview_max_active = models.PositiveIntegerField(default=5)
    preview_enabled = models.BooleanField(default=True)
    trigger_mode = models.CharField(
        max_length=32,
        choices=TriggerMode.choices,
        default=TriggerMode.AUTO_ON_PUSH,
    )
    # Five-field cron expression (minute hour day month weekday). Only
    # meaningful when ``trigger_mode == CRON``; empty otherwise. The
    # scheduled-deploy workflow that consumes this is tracked
    # separately — for now this is captured at registration time so
    # the contract is stable when that workflow lands.
    cron_expression = models.CharField(max_length=120, blank=True, default="")
    # When ``trigger_mode == 'cron'``, this flag pauses scheduled
    # dispatch without flipping the mode (which would also clear the
    # cron expression). The dispatcher tick skips paused apps; manual
    # deploys are unaffected.
    cron_paused = models.BooleanField(default=False)
    deploy_branch = models.CharField(max_length=128, default="main")

    # App-global webhook-deploy pause (#399). Independent of the
    # per-env ``deploys_paused`` (#378) and the per-env ``ingress_paused``
    # axes: this flag short-circuits CI-fired deploys across every
    # environment of the app. The gate fires inside ``start_deployment``
    # and the CI REST endpoint when ``trigger_kind`` is webhook-shaped
    # (push / ci / scheduled). Operator-fired ``manual`` deploys bypass
    # the pause — explicit on-call escape valve so a wedged CI can be
    # stopped without locking the operator out of fixing the app.
    # ``paused_at`` / ``paused_by`` are stamped on the off→on
    # transition so the Settings UI can render "Paused by <user>,
    # <relative time> — reason: <reason>" without joining the audit log.
    # Archive state (#743). When archived, all workload replicas are scaled
    # to zero and webhook/cron deploys are suppressed. ``archived_by`` is
    # the user who triggered the archive (audit trail; not a permission gate).
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="archived_apps",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    webhook_deploys_paused = models.BooleanField(default=False)
    webhook_deploys_paused_at = models.DateTimeField(null=True, blank=True)
    webhook_deploys_paused_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="webhook_deploys_paused_apps",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    webhook_deploys_pause_reason = models.CharField(max_length=512, blank=True, default="")

    # Approval policy (#291). Applies in addition to (and OR'd with)
    # ``AppEnvironment.required_approvals`` — whichever path requires
    # more approvers wins. Empty approver sets with
    # ``requires_approval=True`` mean any user holding the
    # ``app.approve_deploy`` permission on the app's org can approve.
    requires_approval = models.BooleanField(default=False)
    approver_team = models.ForeignKey(
        "astrolift_identity.Team",
        related_name="approver_for_apps",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    approver_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="approver_for_apps",
        blank=True,
    )
    minimum_approvals = models.PositiveIntegerField(default=1)

    # Secret-change approval policy (#488). Parallel to the deployment
    # approval gear above. When ``requires_secret_approval=True``, the
    # secret-write mutations (``setAppSecret`` / ``deleteAppSecret`` /
    # ``attachSecretBundle`` / ``detachSecretBundle``) stop applying
    # directly and instead create a ``SecretChangeProposal`` that needs
    # ``secret_minimum_approvals`` approvers before the underlying op
    # fires.  Empty ``secret_approver_users`` with the flag on means
    # "any user holding the secret-approve permission on the app's
    # org can approve" — the permission gate covers that case.
    requires_secret_approval = models.BooleanField(default=False)
    secret_approver_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name="secret_approver_for_apps",
        blank=True,
    )
    secret_minimum_approvals = models.PositiveIntegerField(default=1)

    # Latest preview screenshot URL for this app, written by the
    # platform's screenshotter service (spec 09 §4.21). Stays empty
    # until the service captures and uploads its first frame; the UI
    # falls back to a branded placeholder while empty.
    preview_screenshot_url = models.URLField(blank=True, default="", max_length=1024)

    # Timestamp of the last operator-initiated "Resync from source"
    # (#386). Distinct from ``updated_at`` which moves on every save;
    # this only advances when the manifest_sync service runs against
    # the repo. Surfaces on the Settings page so operators can see
    # "Last resynced 5 minutes ago" without grepping audit logs.
    last_resync_at = models.DateTimeField(null=True, blank=True)

    # Outcome of the manifest bootstrap at registration (#1553). Registration
    # is deliberately forgiving — a manifest it cannot parse or fetch must not
    # cost the operator the whole registration — but "forgiving" had meant
    # "silent": the app landed with zero workloads and nothing said why, so
    # the first signal was a deploy that shipped nothing much later. Recorded
    # here for the same reason ``autowire_state`` is recorded: the app detail
    # page can then say which step still needs attention.
    #
    # ``applied`` the manifest parsed and its workloads were materialised;
    # ``parse_failed`` an inline manifest was unusable; ``fetch_failed`` /
    # ``diverged`` came back from the repo fetch; ``no_source`` there was
    # nothing to bootstrap from. Empty means the app predates the field.
    #
    # Current state, not a registration audit record (#1692). A later
    # resync that applies clears a failure back to ``applied``: the
    # banner tells the operator what still needs attention, and an app
    # whose manifest has since been fixed and whose workloads exist was
    # otherwise told forever that it had neither. The registration
    # attempt itself stays in the audit log.
    manifest_bootstrap_status = models.CharField(max_length=32, blank=True, default="")
    manifest_bootstrap_error = models.TextField(blank=True, default="")

    # Source-host webhook state (#385). Populated by
    # ``installAstroliftSourceWebhook`` so the Settings page can show
    # whether the push-event hook is wired up. ``source_webhook_id``
    # is the host-side identifier (GitHub returns a numeric id; we
    # store as string so GitLab / Bitbucket slot in alongside).
    # ``source_webhook_installed_at`` advances on each successful
    # install / refresh; the FE renders it as a relative-time chip.
    source_webhook_id = models.CharField(max_length=128, blank=True, default="")
    source_webhook_installed_at = models.DateTimeField(null=True, blank=True)

    # Autowire outcome snapshot (#1108). Registration chains the three
    # repo-wiring steps (CI workflow → source webhook → CI secrets); this
    # sparse blob records the last verified per-step result so the app
    # detail page can render an "autowire incomplete" banner without
    # re-hitting the host on every read. Shape (keys omitted until a step
    # runs): ``{"ci_workflow": "ok"|"error", "webhook": "ok"|"error",
    # "secrets": "ok"|"error", "checked_at": <iso8601>, "errors": {step:
    # message}}``. Empty dict means "autowire never ran" — the read path
    # then derives status from the columns (a legacy app registered before
    # the chain landed reads as unwired, which is the truth). The phantom
    # webhook state (installed_at set + id empty, App not covering the
    # repo) is derived, not stored, so it self-heals on the next run.
    autowire_state = models.JSONField(default=dict, blank=True)

    # Managed CI-workflow sync record (#1209). Part of the bidirectional
    # versioned sync for the astrolift-ci workflow file.
    # ``ci_workflow_template_version`` is the ``TEMPLATE_VERSION`` stamped into
    # the file the platform last reconciled onto this app's repo — indexed so a
    # later sweep can cheaply find apps whose file predates the current template
    # (``template_stale``). Null until the app has been versioned-synced at
    # least once. ``ci_workflow_state`` is a sparse blob written by
    # ``sync_workflow_file_to_repo`` after a successful reconcile (keys omitted
    # until they apply): ``{"synced_hash", "synced_blob_sha", "synced_at",
    # "path", "state", "last_commit_sha"?, "pr_url"?}``. Empty dict means "never
    # versioned-synced" — the DB-only read status then reports ``absent``.
    ci_workflow_template_version = models.IntegerField(null=True, blank=True, db_index=True)
    ci_workflow_state = models.JSONField(default=dict, blank=True)

    # When the platform last pushed the CI secrets bundle to the app's repo
    # (#1221). Captured just BEFORE the secret PUTs land so the host-side
    # ``updated_at`` of every pushed secret is ≥ it; the validate path compares
    # against this to report per-secret freshness (``is_current``). Null means
    # "never pushed (or pushed before this field existed)" — validate then
    # reports freshness as unknown, not stale.
    ci_secrets_pushed_at = models.DateTimeField(null=True, blank=True)

    default_tenant_cluster = models.ForeignKey(
        "astrolift_clusters.TenantCluster",
        related_name="registered_apps",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    # Supply-chain / scanner policy consulted at promote time by the
    # PromoteDeploymentWorkflow (#313). Persisted as a sparse JSON blob
    # so we can add keys without per-toggle migrations; the
    # ``security_policy_resolved`` property fills in the platform
    # defaults for any unset key so callers never need to know which
    # keys are populated. Empty dict means "use defaults everywhere".
    security_policy = models.JSONField(default=dict, blank=True)

    # Per-app egress, opt-in (#1599). Empty means no NetworkPolicy is
    # emitted for this app at all, which is every app today.
    #
    # The opt-in is not caution for its own sake. `render_network_policy`
    # builds a deny-by-default policy -- ingress only from the ingress
    # controller, egress only to DNS, bound managed services and whatever is
    # listed here. Emitting that for an app that has never had a
    # NetworkPolicy silently cuts every egress nobody thought to declare, and
    # the symptom is a production app that cannot reach a third-party API it
    # has always reached. Enabling it has to be somebody's decision.
    #
    # Shape: {"enabled": bool, "allowed_cidrs": [...], "allowed_fqdns": [...],
    #         "extra_internal_cidrs": [...], "source_ip_mode": "...",
    #         "allow_internet_https": bool}
    # Cached DNS resolution result for the app doctor (#1550). The panel
    # reads this instead of resolving live: it renders on every app-detail
    # load, and making that latency a function of DNS is worse than an
    # honest, dated answer.
    #
    # Shape: {"probed_at": iso8601, "unresolved": ["host", ...]}
    # Absent means never probed, which the doctor reports as unknown rather
    # than pass.
    dns_probe = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Cached hostname-resolution probe for the app doctor. Written by "
            "the scheduled prober; read by the doctor with its age."
        ),
    )

    network_policy = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            'Per-app egress rules. Empty or {"enabled": false} emits no '
            "NetworkPolicy. Enabling it applies a deny-by-default posture: "
            "anything not listed here, not a bound managed service, and not "
            "DNS becomes unreachable from this app's pods."
        ),
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"],
                condition=models.Q(deleted_at__isnull=True),
                name="registered_app_slug_unique_active_per_org",
            ),
            models.UniqueConstraint(
                fields=["organization", "source_repo", "manifest_path"],
                condition=models.Q(
                    deleted_at__isnull=True,
                )
                & ~models.Q(source_repo=""),
                name="registered_app_repo_manifest_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "is_active"], name="app_org_active_idx"),
            models.Index(fields=["project"], name="app_project_idx"),
            # Apps-list filters (#481). The list query pages by
            # ``(-created_at, -guid)`` over a row set already narrowed
            # by team / project / provisioning_status, so these are the
            # supporting indexes for the filter axes the FE exposes.
            # All three include the soft-delete predicate columns
            # (``deleted_at`` for the team/project pair) so the partial
            # filter inside ``_apply_apps_list_filters`` stays index-only.
            models.Index(fields=["team", "deleted_at"], name="app_team_deleted_idx"),
            models.Index(fields=["project", "deleted_at"], name="app_project_deleted_idx"),
            models.Index(fields=["provisioning_status"], name="app_provisioning_status_idx"),
        ]

    _PROVISIONING_TRANSITIONS = {
        ProvisioningStatus.PENDING: {
            ProvisioningStatus.PROVISIONING,
            ProvisioningStatus.FAILED,
            ProvisioningStatus.TEARING_DOWN,
        },
        ProvisioningStatus.PROVISIONING: {
            ProvisioningStatus.READY,
            ProvisioningStatus.FAILED,
            ProvisioningStatus.TEARING_DOWN,
        },
        ProvisioningStatus.READY: {
            ProvisioningStatus.PROVISIONING,
            ProvisioningStatus.FAILED,
            ProvisioningStatus.TEARING_DOWN,
        },
        ProvisioningStatus.FAILED: {
            ProvisioningStatus.PROVISIONING,
            ProvisioningStatus.TEARING_DOWN,
        },
        # Teardown is reachable from any live state; once tearing down it
        # completes to DEREGISTERED (or back to FAILED if cleanup errors).
        ProvisioningStatus.TEARING_DOWN: {
            ProvisioningStatus.DEREGISTERED,
            ProvisioningStatus.FAILED,
        },
    }

    @property
    def builds_an_image(self) -> bool:
        """Whether the platform ever needs a registry repo + push role here.

        An app produces an image -- and therefore needs somewhere to push it --
        unless it points at a pre-built one. Two model states mean "pre-built,
        no build step": ``build_mode == none`` (the operator supplies a
        published tag and nothing builds) and ``source_kind == direct_upload``
        (an App Builder promote with no source repo, baked out of band). Both
        ``ci_pushed`` and ``platform_build`` produce images, so they still need
        registry coordinates.

        Lives on the model because it is derived from two model fields and has
        to be read from both a workflow activity and the GraphQL layer. It was
        previously a private helper in the schema module, which meant the
        provisioning activity that actually creates the repository could not
        reach it and created one for every app regardless (#1682).
        """
        if (self.build_mode or "").strip() == RegisteredApp.BuildMode.NONE.value:
            return False
        return (self.source_kind or "").strip() != RegisteredApp.SourceKind.DIRECT_UPLOAD.value

    @property
    def effective_build_strategy(self) -> str:
        """Which builder this deploy should invoke, or ``"off"`` for none.

        The two build axes have to agree and only one was ever read
        (#1687): ``DeployAppWorkflow`` branched on ``build_strategy !=
        "off"`` alone, so an app moved to ``ci_pushed`` kept running a
        full platform build on every deploy -- ``platform_build``
        registration persists a ``dockerfile`` strategy, and a mode-only
        change always lands in that state.

        ``build_mode`` decides *whether* the platform builds;
        ``build_strategy`` only decides *which* builder it uses when it
        does. Deriving the answer from both, here, is what stops the two
        drifting apart again -- and it needs no migration, because an
        inconsistent pair already stored resolves correctly on read.
        """

        if (self.build_mode or "").strip() != RegisteredApp.BuildMode.PLATFORM_BUILD.value:
            return RegisteredApp.BuildStrategy.OFF.value
        return (self.build_strategy or "").strip() or RegisteredApp.BuildStrategy.OFF.value

    @property
    def security_policy_resolved(self) -> dict:
        """Return the supply-chain policy with platform defaults filled in.

        Defaults are deliberately strict: critical CVEs and missing
        cosign signatures block by default. ``block_on_high_cve_threshold``
        is nullable — None means "no count-based block on high-severity
        CVEs". A non-null integer N means "block when the high-CVE count
        on the candidate image is >= N".

        Callers (notably PromoteDeploymentWorkflow's supply-chain gate)
        should always read through this property rather than the raw
        JSON field so that a partial policy persists the operator's
        explicit choices without losing the platform defaults for the
        unspecified knobs.
        """
        policy = self.security_policy or {}
        return {
            "block_on_critical_cves": policy.get("block_on_critical_cves", True),
            "block_on_missing_signature": policy.get("block_on_missing_signature", True),
            "block_on_high_cve_threshold": policy.get("block_on_high_cve_threshold"),
        }

    def transition_provisioning(
        self, new_status: RegisteredApp.ProvisioningStatus, *, reason: str = ""
    ) -> None:
        current = RegisteredApp.ProvisioningStatus(self.provisioning_status)
        # Idempotent: re-marking the current state is a no-op, not an error.
        # Without this, a deregister re-fired to recover an app stuck at
        # TEARING_DOWN (after a failed/terminated teardown) dies at
        # mark_tearing_down on tearing_down → tearing_down, leaving the app
        # permanently unrecoverable (#1006). Same principle as #998.
        if new_status is current:
            return
        allowed = self._PROVISIONING_TRANSITIONS.get(current, set())
        if new_status not in allowed:
            raise ValueError(
                f"RegisteredApp({self.pk}) cannot transition {current.value} → {new_status.value}"
            )
        self.provisioning_status = new_status.value
        if new_status is RegisteredApp.ProvisioningStatus.READY:
            self.provisioning_error = ""
        elif new_status is RegisteredApp.ProvisioningStatus.FAILED:
            # The whole point of the FAILED state. Until #1677 nothing ever
            # reached it and nothing ever wrote this field, so a provision that
            # died looked identical to one still running -- for as long as
            # anyone cared to wait. Truncated because a driver traceback can
            # be long and this is rendered inline by the CLI and the UI.
            self.provisioning_error = reason[:2000]
        self.save(update_fields=["provisioning_status", "provisioning_error", "updated_at", "version"])
