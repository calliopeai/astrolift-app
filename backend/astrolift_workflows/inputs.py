"""
Workflow + activity input/output payloads.

Kept as plain dataclasses with primitive fields so the Temporal
JSON converter handles them without a custom codec. ID-style fields
are integers to match the platform PK shape; GUIDs come along when
they're useful for human readability in the workflow UI.
"""

from __future__ import annotations

import dataclasses
from typing import Any


@dataclasses.dataclass(slots=True, frozen=True)
class Actor:
    kind: str  # user | api_token | deploy_token | system
    user_id: int | None = None
    token_id: int | None = None
    display: str = ""


@dataclasses.dataclass(slots=True, frozen=True)
class OnboardAppInput:
    registered_app_id: int
    actor: Actor
    provider_plugin_id: int
    tenant_cluster_id: int


@dataclasses.dataclass(slots=True, frozen=True)
class DeployAppInput:
    registered_app_id: int
    app_environment_id: int
    deployment_id: int
    image_tags: dict[str, str]
    trigger_kind: str
    actor: Actor
    # Git commit SHA the build step checks out + builds from (#865, #978).
    # Empty on manual/UI deploys with no commit context — the build falls
    # back to the app's default branch. Carried here so the
    # platform-build path has the SHA without re-reading the Deployment row.
    commit_sha: str = ""


@dataclasses.dataclass(slots=True, frozen=True)
class RollbackInput:
    deployment_id: int
    actor: Actor


@dataclasses.dataclass(slots=True, frozen=True)
class RedeployInput:
    """Re-run apply for the latest deployment of (app, env) with the
    same image_tag. The mutation already created the new Deployment
    row; the workflow operates on its id."""

    deployment_id: int
    actor: Actor


@dataclasses.dataclass(slots=True, frozen=True)
class PromoteInput:
    """Move an image_tag from one environment to another with the
    same app — sets ``promoted_from`` on the new deployment so the
    audit log can trace the lineage."""

    source_deployment_id: int
    target_app_environment_id: int
    actor: Actor


@dataclasses.dataclass(slots=True, frozen=True)
class ProvisionManagedServiceInput:
    managed_service_id: int
    actor: Actor


@dataclasses.dataclass(slots=True, frozen=True)
class UpdateManagedServiceInput:
    managed_service_id: int
    actor: Actor


@dataclasses.dataclass(slots=True, frozen=True)
class DeprovisionManagedServiceInput:
    """Input for ``DeprovisionManagedServiceWorkflow`` (#320).

    Two-axis safety matches the SDK Protocol on
    ``ManagedServiceDriver.deprovision``:

      ``delete_data`` controls what happens to persistent state.
        False (default): drivers take the safest deletion path —
        final-snapshot RDS, retain bucket contents, drain queues,
        export Redis backups. The artifact survives for later restore.
        True: irreversibly delete state alongside the resource.

      ``force_destroy`` controls safety guards (Terraform semantic).
        False (default): respect cloud-side deletion-protection flags,
        refuse when guards trip, error with a clear operator message.
        True: bypass guards (suspend versioning, terminate sessions,
        ignore deletion-protection, --atomic cleanup).

    Together they form the four-corner matrix described in the SDK
    docstring. UI surfaces both as separate explicit checkboxes.
    """

    managed_service_id: int
    actor: Actor
    delete_data: bool = False
    force_destroy: bool = False


@dataclasses.dataclass(slots=True, frozen=True)
class BuildPreviewInput:
    """Input for ``BuildPreviewWorkflow`` (#751).

    Dispatched by the ``createPreviewEnvironment`` mutation when an
    operator manually triggers a preview build from the UI, and by the
    GitHub PR webhook handler on opened / synchronize events.
    """

    preview_environment_id: int
    actor: Actor


@dataclasses.dataclass(slots=True, frozen=True)
class TearDownPreviewInput:
    preview_environment_id: int
    actor: Actor


@dataclasses.dataclass(slots=True, frozen=True)
class ValidateCustomDomainInput:
    """Input for ``ValidateCustomDomainWorkflow`` (#397).

    Drives the DNS handshake validation: probes the authoritative
    nameservers for each row in ``CustomDomain.required_dns_records``,
    flips ``propagated=True`` per-row, and transitions the parent
    row's ``validation_status`` to ``validated`` or ``failed`` once
    all required records have propagated (or any one of them has
    failed past the retry budget).

    When the domain's parent zone is a platform-managed ``ManagedDomain``
    the workflow ALSO writes the required records via the bound
    cluster's ``DnsDriver.ensure_record`` before probing.
    """

    custom_domain_id: int
    actor: Actor


@dataclasses.dataclass(slots=True, frozen=True)
class SyncAppDomainInput:
    """Input for ``SyncAppDomainWorkflow`` (#143, spec 06 §4.17).

    Reconciles DNS + ingress + cert state after ``setAppSubdomain``
    changed the app's platform hostname, without a full redeploy.
    ``previous_subdomain`` is what the row held before the write --
    the workflow needs it to compute the hostname diff, and to put
    the routing back if a step fails part-way through.
    """

    registered_app_id: int
    previous_subdomain: str
    actor: Actor


@dataclasses.dataclass(slots=True, frozen=True)
class InstallClusterPrereqsInput:
    """Input for ``InstallClusterPrereqsWorkflow`` (#66).

    Captures the operator's bootstrap-recipe selection so the
    workflow can render Flux HelmReleases for the chosen subset.

    ``selected_components`` is the list of ``BootstrapComponent.key``
    values the operator checked in the cluster-detail bootstrap
    card. ``option_overrides`` is keyed by component.key with an
    inner dict of option.key → chosen value (from option.choices).
    Idempotent: re-running with a different selection converges.
    """

    cluster_id: int
    actor: Actor
    selected_components: tuple[str, ...]
    option_overrides: dict[str, dict[str, str]]


@dataclasses.dataclass(slots=True, frozen=True)
class TearDownAppInput:
    """Input for ``TearDownAppWorkflow`` (#358).

    Symmetric inverse of ``OnboardAppWorkflow``. Fans out the managed-
    service deprovision per binding, deletes the app's k8s namespaces,
    revokes deploy tokens, and soft-deletes the platform rows.

    ``delete_data`` + ``force_destroy`` propagate to the child
    ``DeprovisionManagedServiceWorkflow`` per the SDK's four-corner
    safety matrix. UI surfaces both as separate explicit checkboxes.
    """

    registered_app_id: int
    actor: Actor
    delete_data: bool = False
    force_destroy: bool = False


@dataclasses.dataclass(slots=True, frozen=True)
class DeregisterAppInput:
    """Input for ``DeregisterAppWorkflow`` (#392).

    Hard-deregister of a RegisteredApp: tears down every per-app
    cloud resource (k8s namespace, managed services, registry repo,
    IRSA role, source webhook, deploy tokens, materialized secrets)
    before soft-deleting the platform rows. ``delete_data`` +
    ``force_destroy`` propagate to the child
    ``DeprovisionManagedServiceWorkflow`` per the SDK's four-corner
    safety matrix. Both default to ``False`` (the SAFE corner, matching
    ``TearDownAppInput`` / ``DeprovisionManagedServiceInput``): a default
    deregister takes an RDS final snapshot, retains S3 contents, and
    archives the registry repo — fully recoverable. An operator must
    explicitly opt in (``delete_data=True`` + ``force_destroy=True``) to
    irreversibly wipe persistent state (#1000).

    Idempotent on resume: the workflow id is
    ``DeregisterAppWorkflow-<app-guid>`` so re-firing the mutation
    joins the existing run rather than starting a parallel teardown.
    The workflow reports each step's outcome on
    ``WorkflowResult.data["teardown"][<resource>]`` so an operator
    can inspect what's still live before a retry.
    """

    registered_app_id: int
    actor: Actor
    delete_data: bool = False
    force_destroy: bool = False


@dataclasses.dataclass(slots=True, frozen=True)
class BringClusterIntoManagementInput:
    """Input for ``BringClusterIntoManagementWorkflow`` (#316).

    ``cluster_id`` is the TenantCluster PK — workflows accept ints
    everywhere else, so we match that convention. ``force_preflight``
    is honored on idempotent re-runs: the workflow normally skips the
    Job when the row is already managed, but a Refresh call from the
    UI can flip this to True to re-validate end-to-end.
    """

    cluster_id: int
    actor: Actor
    force_preflight: bool = False


@dataclasses.dataclass(slots=True, frozen=True)
class DecommissionClusterInput:
    """Input for ``DecommissionClusterWorkflow``.

    The workflow refuses to proceed when any active ``AppEnvironment``
    is bound to the cluster — operators must migrate or delete those
    envs first. The decommissioned terminal state preserves the row
    for audit; the cluster is not deploy-eligible from that point.

    ``delete_cloud_infra`` is the explicit opt-in for the destructive
    half of decommission. When False (default) the workflow only
    removes the platform RBAC bundle from the cluster — the cluster
    itself keeps running and is operator-owned. When True the
    workflow ALSO calls into the driver's ``teardown_cluster`` which
    deletes the EKS / GKE / AKS managed cluster (bare-metal stays an
    operator concern). UI surfaces this as a separate confirmation
    checkbox so the destructive path can't be triggered accidentally.
    """

    cluster_id: int
    actor: Actor
    delete_cloud_infra: bool = False


@dataclasses.dataclass(slots=True, frozen=True)
class MigrateAppInput:
    """Input for ``MigrateAppWorkflow``.

    Moves an ``AppEnvironment`` from its current ``tenant_cluster`` to
    ``target_cluster_id``. The workflow applies to the target first,
    waits for rollout + health, then atomically switches the env's
    binding and (when ``drain_source`` is true) deletes the app's
    resources from the source cluster.

    ``deployment_id`` is the latest RUNNING (or PENDING) deployment row
    the workflow re-deploys against the target — it carries the image
    tag, secrets reference set, and rendered config snapshot.
    """

    registered_app_id: int
    app_environment_id: int
    deployment_id: int
    target_cluster_id: int
    drain_source: bool
    actor: Actor


@dataclasses.dataclass(slots=True, frozen=True)
class RotateSecretBundleInput:
    """Rotate one SecretBundle across every cluster/app/env that has
    an active ``AppSecretBundleRef`` to it (#365). The workflow re-
    fetches values from the SecretsBackend, applies them as the
    materialized k8s Secret, then bounces every workload that
    envFroms the bundle so the new values take effect immediately."""

    secret_bundle_id: int
    actor: Actor
    bounce_workloads: bool = True
    """Set False on the scheduled refresh path — restarting every
    workload bound to a rotated bundle on an hourly cadence would be
    too disruptive. Workloads pick up the new values on next deploy
    or pod restart. ``rotateSecretBundle`` (operator-fired) leaves
    this True so the rotation actually takes effect."""


@dataclasses.dataclass(slots=True, frozen=True)
class DeleteSecretBundleFromClustersInput:
    """Fan-out cleanup of the k8s Secret the bundle was materialized
    into on every cluster it ever reached. Fired on bundle soft-
    delete (#365)."""

    secret_bundle_id: int
    actor: Actor


@dataclasses.dataclass(slots=True, frozen=True)
class CreateDevEnvironmentInput:
    """Input for ``CreateDevEnvironmentWorkflow`` (#767).

    Drives the initial provisioning of a Calliope App Builder dev
    environment — namespace + ConfigMap + Deployment + Service + Ingress
    on the bound ``TenantCluster``. The REST endpoint creates the
    ``DevEnvironment`` row in ``creating`` state then starts the workflow;
    the workflow flips the row to ``running`` (or ``failed``) when it
    settles.
    """

    dev_environment_id: int
    actor: Actor


@dataclasses.dataclass(slots=True, frozen=True)
class SyncDevEnvironmentFilesInput:
    """Input for ``SyncDevEnvironmentFilesWorkflow`` (#767).

    Fired when the App Builder pushes a new file tree to a running dev
    environment. The workflow updates the ConfigMap holding the file
    payload and bumps a pod-template annotation so the Deployment rolls
    a fresh pod that picks up the new contents.
    """

    dev_environment_id: int


@dataclasses.dataclass(slots=True, frozen=True)
class ProvisionManagedDomainInput:
    """Input for ``ProvisionManagedDomainWorkflow`` (#781).

    Drives the six-step DNS zone provisioning flow: hosted-zone
    creation, wildcard cert request, cert issuance poll, ManagedDomain
    row registration, the NS-delegation gate, and activation.

    ``cluster_id`` is the TenantCluster PK whose provider plugin's
    ``DnsDriver`` is used for all cloud operations. ``zone`` is the
    DNS zone name to provision (e.g. ``"apps.platform.example"``).

    ``is_platform_managed_zone`` controls whether Step 1
    (``provision_dns_zone``) is executed. When False the caller owns
    the DNS zone and only the cert request + issuance poll are needed;
    Step 1 is skipped and ``zone_id`` is left empty.
    """

    cluster_id: int
    zone: str
    is_platform_managed_zone: bool
    actor: Actor


@dataclasses.dataclass(slots=True, frozen=True)
class DeprovisionManagedDomainInput:
    """Input for ``DeprovisionManagedDomainWorkflow``.

    The inverse of ``ProvisionManagedDomainInput``: tears down the cloud
    resources ``ProvisionManagedDomainWorkflow`` created for ``zone`` —
    the wildcard certificate first, then the hosted zone — via the
    ``DnsDriver`` of the TenantCluster at ``cluster_id``.
    """

    cluster_id: int
    zone: str
    actor: Actor


@dataclasses.dataclass(slots=True, frozen=True)
class WorkflowDefinitionRunInput:
    """Input for ``WorkflowDefinitionRunWorkflow`` — the agent workflow
    stage executor.

    ``workflow_run_id`` is the ``astrolift_operations.WorkflowRun`` mirror
    row's pk (as a string — the executor keys stage executions to it). The
    ``runWorkflowDefinition`` mutation creates the WorkflowInstance +
    WorkflowRun rows first, then passes that pk here.

    ``only_stage_order`` / ``fan_out_index`` are set ONLY on the child runs
    a FAN_OUT parent spawns: each child executes exactly the one stage at
    ``only_stage_order`` and never re-fans-out. Top-level runs leave both
    unset and execute every stage in order.

    ``stage_bindings`` carries the tier-2 ``Workflow``'s per-stage concrete
    bindings (spec 40 §2.2/§3) — ``{stage_order: {agent_workload_id,
    skill_refs, params}}`` — so a run started from a configured Workflow
    freezes which agent, environment, skills, prompt, and output key each
    stage resolves to. Empty for definition-level runs (the legacy
    ``runWorkflowDefinition`` / webhook paths), which use the definition's
    stage defaults.
    """

    workflow_definition_slug: str
    workflow_run_id: str
    trigger_payload: dict[str, Any]
    actor: Actor
    # Exact DB identity freezes which visible definition a configured run
    # selected when an org definition and global template share a slug.
    # Optional for histories created before this field existed.
    workflow_definition_id: str | None = None
    only_stage_order: int | None = None
    fan_out_index: int | None = None
    stage_bindings: dict[str, Any] | None = None
    # Definition PKs already traversed by nested child runs. Runtime checks
    # this immutable chain in addition to import-time graph validation.
    workflow_ancestry: list[str] | None = None


@dataclasses.dataclass(slots=True, frozen=True)
class DispatchAgentTaskInput:
    """Input for ``DispatchAgentTaskWorkflow`` (spec 33, PR-1).

    The Once-dispatch wrapper for a registered agent ``Workload(kind=agent)``.
    The ``runAstroliftAgent`` mutation creates the ``AgentTask`` (in QUEUED,
    with its ``agent_definition`` Workload set) and passes its pk here; the
    workflow drives that single task through the existing spawn -> poll ->
    terminal pipeline via the ``dispatch_agent_task`` activity.

    Workflow id is ``DispatchAgentTaskWorkflow-<task-guid>`` (the mutation
    constructs it that way) so a duplicate fire joins the in-flight run
    instead of spawning a parallel dispatch of the same task.

    ``trigger_payload`` (spec 33, PR-6) carries the per-dispatch input for a
    webhook/event-triggered run — the incoming payload shaped by the
    binding's ``input_mapping`` (see
    ``astrolift_agents.services.workflow_triggers.apply_input_mapping``).
    ``None`` for manual / cron / loop dispatch, which carry no ad-hoc input
    (matching PR-1's ``runAstroliftAgent``, which does not thread a payload).
    The dispatch activity reads from the AgentTask itself, so this rides along
    as the durable record of what input the trigger fired with.
    """

    agent_task_id: int
    actor: Actor
    trigger_payload: dict[str, Any] | None = None


@dataclasses.dataclass(slots=True, frozen=True)
class WorkflowResult:
    ok: bool
    message: str = ""
    data: dict[str, Any] | None = None
