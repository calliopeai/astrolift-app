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
class WorkflowResult:
    ok: bool
    message: str = ""
    data: dict[str, Any] | None = None
