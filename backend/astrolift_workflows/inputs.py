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
    image_tags: dict[str, str]
    trigger_kind: str
    actor: Actor


@dataclasses.dataclass(slots=True, frozen=True)
class RollbackInput:
    deployment_id: int
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
class WorkflowResult:
    ok: bool
    message: str = ""
    data: dict[str, Any] | None = None
