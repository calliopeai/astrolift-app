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
class TearDownPreviewInput:
    preview_environment_id: int
    actor: Actor


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
class WorkflowResult:
    ok: bool
    message: str = ""
    data: dict[str, Any] | None = None
