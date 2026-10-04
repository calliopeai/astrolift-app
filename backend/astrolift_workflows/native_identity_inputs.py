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
