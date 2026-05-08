"""GraphQL types for source connections + SSH deploy keys."""

from __future__ import annotations

import datetime as dt

import strawberry

from astrolift_graphql import GUID


@strawberry.type(name="AstroliftSourceConnection")
class SourceConnectionType:
    id: GUID
    kind: str
    name: str
    display_name: str
    account_login: str
    installation_id: str
    api_base_url: str
    oauth_client_id: str
    oauth_redirect_uri: str
    repo_visibility_scopes: list[str]
    is_oauth_app_config: bool
    is_active: bool
    token_expires_at: dt.datetime | None
    last_used_at: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime


@strawberry.type(name="AstroliftSshDeployKey")
class SshDeployKeyType:
    id: GUID
    name: str
    public_key: str
    fingerprint_sha256: str
    registered_app_slug: str | None  # null when org-scoped
    last_used_at: dt.datetime | None
    is_active: bool
    created_at: dt.datetime


@strawberry.type(name="AstroliftSshDeployKeyCreated")
class SshDeployKeyCreatedType:
    """Returned exactly once on creation. Same shape as
    SshDeployKeyType; we don't expose the private key (operator
    pastes the public key into their SCM, private stays on-server)."""

    key: SshDeployKeyType


def source_connection_to_type(c) -> SourceConnectionType:
    return SourceConnectionType(
        id=GUID(str(c.guid)),
        kind=c.kind,
        name=c.name,
        display_name=c.display_name or "",
        account_login=c.account_login or "",
        installation_id=c.installation_id or "",
        api_base_url=c.api_base_url or "",
        oauth_client_id=c.oauth_client_id or "",
        oauth_redirect_uri=c.oauth_redirect_uri or "",
        repo_visibility_scopes=list(c.repo_visibility_scopes or []),
        is_oauth_app_config=c.is_oauth_app_config,
        is_active=c.is_active,
        token_expires_at=c.token_expires_at,
        last_used_at=c.last_used_at,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


def ssh_key_to_type(k) -> SshDeployKeyType:
    return SshDeployKeyType(
        id=GUID(str(k.guid)),
        name=k.name,
        public_key=k.public_key,
        fingerprint_sha256=k.fingerprint_sha256,
        registered_app_slug=(
            k.registered_app.slug if k.registered_app_id else None
        ),
        last_used_at=k.last_used_at,
        is_active=k.is_active,
        created_at=k.created_at,
    )
