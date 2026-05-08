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

    # NULL user → org-level credential (PAT, OAuth-app config, App
    # install). Set → personal credential created by an OAuth dance.
    is_personal: bool
    user_username: str | None
    parent_oauth_app_id: GUID | None


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


@strawberry.type(name="AstroliftRemoteRepo")
class RemoteRepoType:
    """One row of a SCM-host's repo list, as the operator picks
    in /apps/new. Normalized across hosts so the picker UI is the
    same shape regardless of provider."""

    full_name: str
    name: str
    description: str
    default_branch: str
    visibility: str
    clone_url_https: str
    clone_url_ssh: str
    web_url: str
    is_archived: bool
    is_fork: bool
    pushed_at: str | None


@strawberry.type(name="AstroliftRemoteRepoList")
class RemoteRepoListType:
    """Wrapper carrying either a successful list or a recoverable
    error code so the UI can show 'reconnect' affordances inline
    instead of treating every transient API blip as a 500."""

    repos: list[RemoteRepoType]
    error_code: str | None
    error_message: str | None
    recoverable: bool


@strawberry.type(name="AstroliftSshDeployKeyCreated")
class SshDeployKeyCreatedType:
    """Returned exactly once on creation. Same shape as
    SshDeployKeyType; we don't expose the private key (operator
    pastes the public key into their SCM, private stays on-server)."""

    key: SshDeployKeyType


def source_connection_to_type(c) -> SourceConnectionType:
    parent_guid: GUID | None = None
    if c.parent_oauth_app_id:
        parent_guid = GUID(str(c.parent_oauth_app.guid))
    user_username: str | None = None
    if c.user_id:
        user_username = c.user.username
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
        is_personal=c.user_id is not None,
        user_username=user_username,
        parent_oauth_app_id=parent_guid,
    )


def ssh_key_to_type(k) -> SshDeployKeyType:
    return SshDeployKeyType(
        id=GUID(str(k.guid)),
        name=k.name,
        public_key=k.public_key,
        fingerprint_sha256=k.fingerprint_sha256,
        registered_app_slug=(k.registered_app.slug if k.registered_app_id else None),
        last_used_at=k.last_used_at,
        is_active=k.is_active,
        created_at=k.created_at,
    )


def remote_repo_to_type(r) -> RemoteRepoType:
    return RemoteRepoType(
        full_name=r.full_name,
        name=r.name,
        description=r.description,
        default_branch=r.default_branch,
        visibility=r.visibility,
        clone_url_https=r.clone_url_https,
        clone_url_ssh=r.clone_url_ssh,
        web_url=r.web_url,
        is_archived=r.is_archived,
        is_fork=r.is_fork,
        pushed_at=r.pushed_at,
    )
