"""Metadata-only original caller references for native identity workflows."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AcceptedAppIdentityAuthority:
    organization_guid: str
    actor_user_id: int
    environment_guid: str
    app_guid: str
    team_guid: str | None
    project_guid: str | None
    cluster_guid: str
    provider_guid: str
    permission: str
    credential_kind: str
    credential_guid: str
    credential_binding: str
    accepted_token_scopes: tuple[str, ...]
    token_team_guid: str | None
    signature: str
    schema: int = 1


@dataclass(frozen=True, slots=True)
class DeploymentAuthorityContext:
    """A private receipt lookup key, never caller-supplied approval evidence."""

    deployment_guid: str


@dataclass(frozen=True)
class NativeConfigurationReceipt:
    """GUIDs, hashes and observation flags only; it grants no authority."""

    deployment_id: str
    operation_id: str
    source_id: str
    preparation_id: str
    iam_id: str
    apply_id: str
    plan_sha256: str
    configuration_observed: bool
    runtime_observation_reason: str
    workload_ready: bool = False
    invocation_verified: bool = False
