"""SCM mutations: connect a host, paste a PAT, generate an SSH key."""

from __future__ import annotations

import re

import strawberry
from django.db import transaction
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Organization
from astrolift_registry.models import RegisteredApp
from astrolift_registry.schema.types import (
    AppCiWorkflowSyncStatusType,
    build_ci_workflow_sync_status,
)
from astrolift_scm.keygen import generate_ed25519_keypair
from astrolift_scm.models import ScmWebhookInstallation, SourceConnection, SshDeployKey
from astrolift_scm.schema.types import (
    ScmWebhookInstallationType,
    SourceConnectionType,
    SshDeployKeyCreatedType,
    SshDeployKeyType,
    source_connection_to_type,
    ssh_key_to_type,
    webhook_install_to_type,
)
from core.decorators import tenant_scoped
from core.mutations import ErrorCode, mutation_audit
from core.permissions import Permission, require_permission
from core.secrets import encrypt_at_rest
from core.tenancy import get_current_tenant

# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


_KIND_CHOICES = {k.value for k in SourceConnection.Kind}
_VISIBILITY_CHOICES = {k.value for k in SourceConnection.VisibilityScope}

# GitHub App Client ID shape (e.g. ``Iv23lic8662KXwe4XKEI``) — the
# Iv-prefixed form GitHub now mints for App registrations. The legacy
# OAuth-App Client ID is a 20-char lowercase hex string. Both are
# accepted; anything else is operator paste-error and we reject early
# so the OAuth dance doesn't 404 at GitHub later.
_GITHUB_CLIENT_ID_NEW = re.compile(r"^Iv\d+[A-Za-z0-9]+$")
_GITHUB_CLIENT_ID_LEGACY = re.compile(r"^[a-f0-9]{20}$")
# Kinds that require ``app_client_id`` (the GitHub OAuth dance reads
# from this column; see scm_oauth.github_start).
_KINDS_REQUIRING_APP_CLIENT_ID = {
    SourceConnection.Kind.GITHUB_OAUTH_APP.value,
    SourceConnection.Kind.GITHUB_APP_INSTALL.value,
}


def _looks_like_github_client_id(value: str) -> bool:
    return bool(_GITHUB_CLIENT_ID_NEW.match(value) or _GITHUB_CLIENT_ID_LEGACY.match(value))


# Must match auth1.scm_app_manifest._APP_DISPLAY_PREFIX so the #1122 dedup's
# slug recovery (_app_slug_from_connection) treats a BYO-adopted App the same
# as a Bootstrap-created one. Kept as a literal here (not imported) to avoid an
# astrolift_scm → auth1 import cycle.
_APP_DISPLAY_PREFIX = "GitHub App: "


@strawberry.input
class ConnectSourceInput:
    """Configure a host (OAuth-app config) or paste a PAT.

    For OAuth-app config rows (``kind`` ends in ``_oauth_app`` and no
    ``account_login``), the secret holds the OAuth app's client
    secret. For PAT rows, the secret holds the PAT itself. For
    GitHub-App-install rows, the secret holds the app's private-key
    PEM (used to mint installation tokens).
    """

    kind: str
    display_name: str | None = None
    account_login: str | None = None
    installation_id: str | None = None
    api_base_url: str | None = None
    secret_plaintext: str  # PAT, OAuth client secret, or GitHub App PEM
    oauth_client_id: str | None = None
    # GitHub App OAuth Client ID (e.g. ``Iv23lic8662KXwe4XKEI``).
    # Distinct from ``oauth_client_id`` which on github_app_install rows
    # carries the numeric App ID. Required for github_oauth_app +
    # github_app_install kinds; ignored otherwise. See #525.
    app_client_id: str | None = None
    oauth_redirect_uri: str | None = None
    repo_visibility_scopes: list[str] | None = None


@strawberry.input
class UpdateSourceConnectionInput:
    id: GUID
    display_name: str | None = None
    repo_visibility_scopes: list[str] | None = None
    is_active: bool | None = None
    rotate_secret_plaintext: str | None = None
    # Letting operators add the Client ID to an existing connection
    # without rotating credentials — the prod-blocker recovery path
    # for connections that pre-date the column.
    app_client_id: str | None = None


@strawberry.input
class ConnectExistingGithubAppInput:
    """Adopt an EXISTING GitHub App (BYO).

    The operator already created the App on GitHub and installed it on their
    org; they paste its numeric App ID + private-key PEM here (plus, if the
    App is used for the user-to-server OAuth dance, the Client ID / secret /
    webhook secret). Distinct from the manifest **Bootstrap** flow
    (auth1.scm_app_manifest) which *creates* a brand-new App.

    ``org_login`` is optional: when the App has a single installation we adopt
    it unambiguously, otherwise we need the login to pick which installation
    belongs to this org.
    """

    app_id: str
    private_key_pem: str
    client_id: str | None = None
    client_secret: str | None = None
    webhook_secret: str | None = None
    api_base_url: str | None = None
    org_login: str | None = None


@strawberry.input
class DisconnectSourceInput:
    id: GUID


@strawberry.input
class GenerateSshDeployKeyInput:
    name: str
    app_slug: str | None = None  # None => org-scoped


@strawberry.input
class DeleteSshDeployKeyInput:
    id: GUID


@strawberry.input
class RotateWebhookSecretInput:
    connection_id: GUID


@strawberry.input
class InstallScmWebhookInput:
    """Install a webhook on the remote repo through ``connection_id``.

    ``target_url`` defaults to the platform's per-connection webhook
    ingress path (the same URL surfaced by ``rotateWebhookSecret``);
    callers may override for self-hosted reverse proxies. ``secret``
    defaults to the connection's existing webhook secret — if neither
    a secret is stored nor one is provided, the mutation refuses
    (HMAC verification would always fail).
    """

    connection_id: GUID
    repo_full_name: str
    target_url: str | None = None
    secret: str | None = None


@strawberry.input
class PushCiWorkflowInput:
    """Reconcile the managed Astrolift CI workflow into ``app_id``'s repo.

    ``push_ci_workflow`` now delegates to System B
    (``sync_workflow_file_to_repo``), which resolves the org-level write
    connection, deploy branch, path and commit message from the app itself —
    so only ``app_id`` is load-bearing. ``connection_id`` (and the
    ``branch`` / ``commit_message`` / ``file_path`` overrides) are retained for
    API/wizard compatibility but no longer consulted.
    """

    app_id: GUID
    connection_id: GUID
    branch: str | None = None
    commit_message: str | None = None
    file_path: str | None = None


@strawberry.type(name="AstroliftScmPushCiWorkflowResult")
class PushCiWorkflowResult:
    commit_sha: str
    file_path: str
    repo_url: str


@strawberry.input
class CiWorkflowSyncActionInput:
    """Target an app for a managed-CI-workflow drift action (#1210).

    Shared by the three manual drift mutations (resync / adopt / refresh);
    each takes only the app's public guid — the connection and paths are
    resolved from the app's persisted source config, same as Phase 1's push.
    """

    app_id: GUID


@strawberry.type(name="AstroliftCiWorkflowResyncAllResult")
class CiWorkflowResyncAllResult:
    """Fleet-wide resync sweep summary (#1211, Phase 3).

    Counts by resulting state — each managed app lands in exactly one bucket,
    so ``scanned`` equals the sum of the rest. ``pushed`` = apps whose file
    was ``template_stale`` / ``absent`` and had the current template
    reconciled onto the repo; ``skipped`` = apps a fetch error / rate limit /
    transient push failure left untouched; ``failed`` = apps an unexpected
    error isolated. ``in_sync`` / ``repo_drift`` / ``conflict`` / ``unknown``
    mirror the observe-only drift states, which are recorded but NEVER pushed
    (operator hand-edits are preserved).
    """

    scanned: int
    pushed: int
    in_sync: int
    repo_drift: int
    conflict: int
    unknown: int
    skipped: int
    failed: int


# Cap for the on-demand fleet resync (``resyncAllAstroliftCiWorkflows``). The
# mutation sweeps inline (synchronously) so an admin gets counts back in one
# call even while the ``CI_WORKFLOW_RESYNC`` schedule is held; the cap bounds
# the request's fan-out. The opt-in Temporal schedule is the unbounded periodic
# path once an operator enables it.
RESYNC_ALL_INLINE_LIMIT = 250


@strawberry.type(name="AstroliftScmWebhookSecretReveal")
class WebhookSecretReveal:
    """Plaintext secret returned once on rotation; never re-fetchable.
    Operator copies it into the SCM host's webhook config alongside
    the URL we surface alongside it.

    Named ``AstroliftScm…`` rather than ``WebhookSecretReveal`` to
    avoid colliding with the legacy operations-app webhook
    subscription reveal type at the GraphQL surface."""

    connection_id: GUID
    plaintext_secret: str
    webhook_url_path: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_connect(input: ConnectSourceInput):
    if input.kind not in _KIND_CHOICES:
        return gql_failure(
            ErrorCode.VALIDATION.value,
            f"unknown kind {input.kind!r}",
            field="kind",
        )
    if not input.secret_plaintext:
        return gql_failure(
            ErrorCode.VALIDATION.value,
            "secret_plaintext is required",
            field="secretPlaintext",
        )
    for scope in input.repo_visibility_scopes or []:
        if scope not in _VISIBILITY_CHOICES:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"unknown visibility scope {scope!r}",
                field="repoVisibilityScopes",
            )
    if input.kind.endswith("_oauth_app") and not input.account_login:
        # OAuth-app config rows: client_id is required, account_login
        # is empty until a user does the OAuth dance.
        if not input.oauth_client_id:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "oauth_client_id is required for OAuth-app config",
                field="oauthClientId",
            )
    if input.kind in _KINDS_REQUIRING_APP_CLIENT_ID:
        # GitHub OAuth dance + JWT iss claim both want the Client ID
        # (e.g. ``Iv23lic8662KXwe4XKEI``). Per #525 we require it
        # up-front so the connection works end-to-end instead of
        # 404-ing at github.com when the user clicks "Connect my
        # GitHub".
        if not input.app_client_id:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                f"app_client_id (GitHub App Client ID) is required for {input.kind!r}",
                field="appClientId",
            )
        if not _looks_like_github_client_id(input.app_client_id.strip()):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "app_client_id doesn't look like a GitHub App Client ID "
                "(expected Iv… for new Apps or 20-char hex for legacy "
                "OAuth Apps)",
                field="appClientId",
            )
    return None


def _derive_org_login(org_id: int) -> str:
    """Best-effort org GitHub login from an existing active github
    connection. Used only as a hint to disambiguate which installation to
    adopt when the App is installed on more than one account and the operator
    didn't pass ``org_login`` — never authoritative (the persisted
    ``account_login`` always comes from the matched installation itself)."""
    row = (
        SourceConnection.objects.filter(
            organization_id=org_id,
            kind__startswith="github",
            is_active=True,
            deleted_at__isnull=True,
        )
        .exclude(account_login="")
        .order_by("created_at", "pk")
        .first()
    )
    return row.account_login if row else ""


def _match_installation(installations: list[dict], login: str) -> dict | None:
    """The installation whose ``account.login`` equals ``login`` (case-insensitive)."""
    want = login.strip().lower()
    if not want:
        return None
    for inst in installations:
        account = inst.get("account") or {}
        if (account.get("login") or "").strip().lower() == want:
            return inst
    return None


def _get_or_create_app_install(org, *, app_id: str, account_login: str) -> SourceConnection:
    """Idempotent target row for the BYO adopt flow. Re-adopt targets, in order:

      1. the same App (matched by numeric App ID) already on this org, else
      2. any existing ``github_app_install`` on the same GitHub ``account_login``
         — e.g. the dead auto-created App connection from the manifest flow;
         adopting the real App reuses that row and clears its orphan state,
         which is exactly #1126's "auto-clear the dead connection".

    The ``(org, kind, account_login)`` unique constraint permits at most one
    App row per account, so reusing it is the only non-colliding option. Falls
    through to a fresh (unsaved) row when neither exists."""
    base = SourceConnection.objects.filter(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_APP_INSTALL.value,
        deleted_at__isnull=True,
    )
    conn = base.filter(oauth_client_id=app_id).order_by("created_at", "pk").first()
    if conn is None and account_login:
        conn = base.filter(account_login=account_login).order_by("created_at", "pk").first()
    if conn is None:
        conn = SourceConnection(
            organization=org,
            kind=SourceConnection.Kind.GITHUB_APP_INSTALL.value,
        )
    return conn


# ---------------------------------------------------------------------------
# Root mutation
# ---------------------------------------------------------------------------


def _ci_drift_app(app_id) -> RegisteredApp | None:
    """Org-scoped app lookup for the drift actions. Slugs/guids are
    unique only within an org; fails closed (returns None) when the
    tenant has no organization (#1183)."""
    tenant = get_current_tenant()
    org_id = tenant.organization_id if tenant else None
    if org_id is None:
        return None
    return (
        RegisteredApp.objects.filter(
            guid=str(app_id),
            organization_id=org_id,
            deleted_at__isnull=True,
        )
        .select_related("organization")
        .first()
    )


@strawberry.type
class ScmMutation:
    @strawberry.field
    @mutation_audit(action="scm.connect")
    @require_permission(Permission.SCM_CONNECT)
    @tenant_scoped()
    def connect_source(
        self, info: Info, input: ConnectSourceInput
    ) -> MutationResultType[SourceConnectionType]:
        err = _validate_connect(input)
        if err is not None:
            return err

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no organization")

        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        encrypted = encrypt_at_rest(input.secret_plaintext.encode("utf-8"))

        with transaction.atomic():
            conn = SourceConnection.objects.create(
                organization=org,
                kind=input.kind,
                display_name=(input.display_name or "")[:200],
                account_login=(input.account_login or "")[:200],
                installation_id=(input.installation_id or "")[:64],
                api_base_url=input.api_base_url or "",
                oauth_client_id=input.oauth_client_id or "",
                app_client_id=(input.app_client_id or "").strip()[:64],
                oauth_redirect_uri=input.oauth_redirect_uri or "",
                repo_visibility_scopes=list(input.repo_visibility_scopes or []),
                secret_backend_kind=encrypted.backend_kind,
                secret_ciphertext=encrypted.backend_ref,
                is_active=True,
            )
        return gql_success(source_connection_to_type(conn))

    @strawberry.field
    @mutation_audit(action="scm.connect_existing_github_app")
    @require_permission(Permission.SCM_CONNECT)
    @tenant_scoped()
    def connect_existing_github_app(
        self, info: Info, input: ConnectExistingGithubAppInput
    ) -> MutationResultType[SourceConnectionType]:
        """Adopt an existing GitHub App (BYO).

        Validates the creds end-to-end BEFORE persisting: mint an App JWT from
        the PEM → discover the org's installation of the App → mint a real
        installation access token to prove the creds work. Only then do we
        store an encrypted ``github_app_install`` connection. Idempotent per
        (org, app_id): re-running re-adopts / rotates the creds on the existing
        row instead of duplicating. Never logs or returns the PEM / secrets.
        """
        from django.db import IntegrityError

        from astrolift_scm.providers import github_app as gh_app

        app_id = (input.app_id or "").strip()
        if not app_id.isdigit():
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "appId must be the numeric GitHub App ID (e.g. 3705068)",
                field="appId",
            )

        client_id = (input.client_id or "").strip()
        if client_id and not _looks_like_github_client_id(client_id):
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "clientId doesn't look like a GitHub App Client ID "
                "(expected Iv… for new Apps or 20-char hex for legacy OAuth Apps)",
                field="clientId",
            )

        pem = (input.private_key_pem or "").strip()
        if not pem:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "privateKeyPem is required",
                field="privateKeyPem",
            )

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no organization")
        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        # GitHub accepts either the numeric App ID or the OAuth Client ID as
        # the JWT `iss`; prefer the Client ID when supplied (matches the
        # provider's own issuer preference on persisted rows).
        issuer = client_id or app_id
        pem_bytes = pem.encode("utf-8")
        try:
            jwt_token = gh_app._mint_jwt(issuer, pem_bytes)
        except Exception:
            # Any failure loading/signing with the PEM (malformed, encrypted,
            # non-RSA) is operator paste-error — return a clean VALIDATION
            # result, never a 500. The PEM is never logged.
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "privateKeyPem is not a valid RSA private key",
                field="privateKeyPem",
            )

        api_base = (input.api_base_url or "").strip().rstrip("/") or gh_app.GITHUB_API_DEFAULT

        installations, err = gh_app.list_app_installations(api_base, jwt_token)
        if err is not None:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"couldn't list installations for App {app_id}: {err.message}",
                field="appId",
            )
        logins = [x for x in ((i.get("account") or {}).get("login", "") for i in installations) if x]

        explicit_login = (input.org_login or "").strip()
        # A derived login is only a hint (see _derive_org_login); an explicit
        # one is authoritative. So an explicit login that matches nothing is a
        # hard error — we never silently adopt a different install — whereas a
        # non-matching derived login falls through to single-install / ambiguity
        # handling below.
        target_login = explicit_login or _derive_org_login(org_id)
        matched = _match_installation(installations, target_login) if target_login else None
        if matched is None:
            if explicit_login:
                return gql_failure(
                    ErrorCode.PRECONDITION.value,
                    f"GitHub App {app_id} is not installed on {explicit_login!r} "
                    f"(installed on: {', '.join(logins) or 'nothing'})",
                    field="orgLogin",
                )
            if not installations:
                return gql_failure(
                    ErrorCode.PRECONDITION.value,
                    f"GitHub App {app_id} has no installations — install it on your "
                    "org on GitHub, then connect it here",
                    field="appId",
                )
            if len(installations) == 1:
                # Unambiguous: the App is installed exactly once.
                matched = installations[0]
            else:
                return gql_failure(
                    ErrorCode.VALIDATION.value,
                    f"GitHub App {app_id} is installed on multiple accounts "
                    f"({', '.join(logins)}); pass orgLogin to pick one",
                    field="orgLogin",
                )

        installation_id = str(matched.get("id") or "").strip()
        account = matched.get("account") or {}
        account_login = (account.get("login") or "").strip()
        if not installation_id:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"GitHub returned an installation with no id for App {app_id}",
                field="appId",
            )

        # Prove the creds end-to-end before persisting: mint a real
        # installation access token. A failure here means we never store a
        # broken connection.
        try:
            gh_app._exchange_for_installation_token(api_base, jwt_token, installation_id)
        except gh_app.GithubAppError as exc:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"couldn't authenticate as App {app_id} on "
                f"{account_login or 'the installation'}: {exc.message}",
                field="privateKeyPem",
            )

        # Human-friendly name from the App slug (best-effort — a metadata blip
        # shouldn't fail an otherwise-valid adopt).
        app_meta, _meta_err = gh_app.fetch_app_metadata(api_base, jwt_token)
        app_slug = (app_meta.get("slug") or "").strip() if app_meta else ""
        display_name = f"{_APP_DISPLAY_PREFIX}{app_slug or app_id}"

        pem_enc = encrypt_at_rest(pem_bytes)
        try:
            with transaction.atomic():
                conn = _get_or_create_app_install(org, app_id=app_id, account_login=account_login)
                conn.oauth_client_id = app_id  # numeric App ID — webhook-payload lookups
                if client_id:
                    conn.app_client_id = client_id[:64]  # OAuth Client ID — /authorize + JWT iss
                conn.installation_id = installation_id[:64]
                conn.account_login = account_login[:200]
                if input.api_base_url:
                    conn.api_base_url = api_base
                conn.display_name = display_name[:200]
                conn.secret_backend_kind = pem_enc.backend_kind
                conn.secret_ciphertext = pem_enc.backend_ref
                if input.client_secret:
                    cs_enc = encrypt_at_rest(input.client_secret.encode("utf-8"))
                    conn.oauth_client_secret_backend_kind = cs_enc.backend_kind
                    conn.oauth_client_secret_ciphertext = cs_enc.backend_ref
                if input.webhook_secret:
                    ws_enc = encrypt_at_rest(input.webhook_secret.encode("utf-8"))
                    conn.webhook_secret_backend_kind = ws_enc.backend_kind
                    conn.webhook_secret_ciphertext = ws_enc.backend_ref
                conn.is_active = True
                # We just proved the creds live — clear any stale orphan /
                # reauth state so a re-adopt heals a previously-dead row.
                conn.is_orphaned = False
                conn.orphaned_at = None
                conn.orphaned_reason = ""
                conn.reauth_required = False
                conn.save()
        except IntegrityError:
            return gql_failure(
                ErrorCode.CONFLICT.value,
                f"a different GitHub App connection already exists for {account_login!r} in this org",
                field="appId",
            )

        return gql_success(source_connection_to_type(conn))

    @strawberry.field
    @mutation_audit(action="scm.update")
    @require_permission(Permission.SCM_CONNECT)
    @tenant_scoped()
    def update_source_connection(
        self, info: Info, input: UpdateSourceConnectionInput
    ) -> MutationResultType[SourceConnectionType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no organization")
        conn = SourceConnection.objects.filter(
            guid=str(input.id), organization_id=org_id, deleted_at__isnull=True
        ).first()
        if conn is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "connection not found")

        if input.display_name is not None:
            conn.display_name = input.display_name[:200]
        if input.repo_visibility_scopes is not None:
            for scope in input.repo_visibility_scopes:
                if scope not in _VISIBILITY_CHOICES:
                    return gql_failure(
                        ErrorCode.VALIDATION.value,
                        f"unknown visibility scope {scope!r}",
                        field="repoVisibilityScopes",
                    )
            conn.repo_visibility_scopes = list(input.repo_visibility_scopes)
        if input.is_active is not None:
            conn.is_active = input.is_active
        if input.rotate_secret_plaintext:
            encrypted = encrypt_at_rest(input.rotate_secret_plaintext.encode("utf-8"))
            conn.secret_backend_kind = encrypted.backend_kind
            conn.secret_ciphertext = encrypted.backend_ref
        if input.app_client_id is not None:
            # ``""`` is a valid update target (clearing) — but if the
            # row is a GitHub kind, validate the shape before saving so
            # we surface paste-errors at edit time rather than at the
            # next OAuth click.
            new_client_id = input.app_client_id.strip()
            if new_client_id and conn.kind in _KINDS_REQUIRING_APP_CLIENT_ID:
                if not _looks_like_github_client_id(new_client_id):
                    return gql_failure(
                        ErrorCode.VALIDATION.value,
                        "app_client_id doesn't look like a GitHub App "
                        "Client ID (expected Iv… for new Apps or "
                        "20-char hex for legacy OAuth Apps)",
                        field="appClientId",
                    )
            conn.app_client_id = new_client_id[:64]
        conn.save()
        return gql_success(source_connection_to_type(conn))

    @strawberry.field
    @mutation_audit(action="scm.disconnect")
    @require_permission(Permission.SCM_DISCONNECT)
    @tenant_scoped()
    def disconnect_source(
        self, info: Info, input: DisconnectSourceInput
    ) -> MutationResultType[SourceConnectionType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no organization")
        conn = SourceConnection.objects.filter(
            guid=str(input.id), organization_id=org_id, deleted_at__isnull=True
        ).first()
        if conn is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "connection not found")
        conn.soft_delete()
        return gql_success(source_connection_to_type(conn))

    @strawberry.field
    @mutation_audit(action="scm.ssh_key.generate")
    @require_permission(Permission.SCM_KEY_CREATE)
    @tenant_scoped()
    def generate_ssh_deploy_key(
        self, info: Info, input: GenerateSshDeployKeyInput
    ) -> MutationResultType[SshDeployKeyCreatedType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no organization")
        org = Organization.objects.filter(pk=org_id).first()
        if org is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "organization not found")

        app = None
        if input.app_slug:
            app = RegisteredApp.objects.filter(
                organization=org, slug=input.app_slug, deleted_at__isnull=True
            ).first()
            if app is None:
                return gql_failure(
                    ErrorCode.NOT_FOUND.value,
                    f"app {input.app_slug!r} not found",
                    field="appSlug",
                )

        if not input.name.strip():
            return gql_failure(ErrorCode.VALIDATION.value, "name is required", field="name")

        comment = f"astrolift:{org.slug}"
        if app is not None:
            comment += f"/{app.slug}"
        keypair = generate_ed25519_keypair(comment=comment)

        encrypted = encrypt_at_rest(keypair.private_pem)

        with transaction.atomic():
            row = SshDeployKey.objects.create(
                organization=org,
                registered_app=app,
                name=input.name.strip()[:200],
                public_key=keypair.public_openssh,
                fingerprint_sha256=keypair.fingerprint_sha256,
                secret_backend_kind=encrypted.backend_kind,
                private_key_ciphertext=encrypted.backend_ref,
                is_active=True,
            )

        return gql_success(SshDeployKeyCreatedType(key=ssh_key_to_type(row)))

    @strawberry.field
    @mutation_audit(action="scm.webhook.rotate")
    @require_permission(Permission.SCM_CONNECT)
    @tenant_scoped()
    def rotate_webhook_secret(
        self, info: Info, input: RotateWebhookSecretInput
    ) -> MutationResultType[WebhookSecretReveal]:
        """Generate a fresh webhook secret for the connection.

        Returns the plaintext exactly once; from that response on,
        the platform only knows the ciphertext. The operator pastes
        the plaintext into the SCM host's webhook config along with
        the URL we surface here.

        Calling this on a connection that already has a secret
        rotates: any in-flight webhooks signed with the old secret
        will fail HMAC verification and 401, which is the desired
        revocation behavior.
        """
        import secrets

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no organization")
        conn = SourceConnection.objects.filter(
            guid=str(input.connection_id), organization_id=org_id, deleted_at__isnull=True
        ).first()
        if conn is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "connection not found")
        if not conn.kind.startswith(("github_", "gitlab_")):
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"webhooks for {conn.kind!r} aren't supported yet",
            )

        plaintext = secrets.token_urlsafe(32)
        encrypted = encrypt_at_rest(plaintext.encode("utf-8"))
        conn.webhook_secret_backend_kind = encrypted.backend_kind
        conn.webhook_secret_ciphertext = encrypted.backend_ref
        conn.save(
            update_fields=[
                "webhook_secret_backend_kind",
                "webhook_secret_ciphertext",
                "updated_at",
                "version",
            ]
        )

        host = "github" if conn.kind.startswith("github_") else "gitlab"
        path = f"/app/auth1/scm/{host}/webhook/{conn.guid}/"

        return gql_success(
            WebhookSecretReveal(
                connection_id=GUID(str(conn.guid)),
                plaintext_secret=plaintext,
                webhook_url_path=path,
            )
        )

    @strawberry.field
    @mutation_audit(action="scm.webhook.install")
    @require_permission(Permission.SCM_CONNECT)
    @tenant_scoped()
    def install_scm_webhook(
        self, info: Info, input: InstallScmWebhookInput
    ) -> MutationResultType[ScmWebhookInstallationType]:
        """Install a repo webhook on the remote SCM host through
        ``connection_id``.

        GitHub-App-install connections short-circuit (the App's own
        webhook fires for every repo the operator picked at install
        time); we still persist a row so the UI can show
        "Already installed via App" idempotently.

        For PAT / OAuth-user connections we mint a webhook secret
        (or reuse the connection's stored one), POST to the host's
        hooks API, and persist the host-side ``hook_id`` for later
        rotate / delete.
        """
        from django.conf import settings as django_settings

        from astrolift_scm.providers import ProviderError, install_webhook
        from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no organization")

        conn = (
            SourceConnection.objects.filter(
                guid=str(input.connection_id),
                organization_id=org_id,
                deleted_at__isnull=True,
            )
            .select_related("organization")
            .first()
        )
        if conn is None:
            return gql_failure(
                ErrorCode.NOT_FOUND.value,
                "connection not found",
                field="connectionId",
            )
        if not conn.is_active:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "connection is inactive; reconnect first",
                field="connectionId",
            )
        if conn.is_orphaned:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "connection is orphaned (upstream revoked or uninstalled)",
                field="connectionId",
            )
        if conn.is_oauth_app_config:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                "this is an OAuth-app config row; complete the OAuth dance first",
                field="connectionId",
            )

        repo = (input.repo_full_name or "").strip()
        if not repo:
            return gql_failure(
                ErrorCode.VALIDATION.value,
                "repoFullName is required",
                field="repoFullName",
            )

        host = (
            "github"
            if conn.kind.startswith("github_")
            else "gitlab"
            if conn.kind.startswith("gitlab_")
            else None
        )
        if host is None:
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"webhook install for {conn.kind!r} not implemented yet",
                field="connectionId",
            )

        # Default target URL = the platform's per-connection ingress
        # path. Operators behind a reverse proxy override via input.
        # Note APP_BASE_URL may be empty in local dev; we still emit
        # the path so the test stack can assert the right structure.
        app_base = (getattr(django_settings, "APP_BASE_URL", "") or "").rstrip("/")
        default_path = f"/app/auth1/scm/{host}/webhook/{conn.guid}/"
        target_url = (input.target_url or "").strip() or f"{app_base}{default_path}"

        # GitHub-App installs deliver via the App's own webhook —
        # short-circuit + persist a marker row so the UI flips its
        # "Install webhook" affordance to "Already installed".
        if conn.kind == "github_app_install":
            with transaction.atomic():
                existing = ScmWebhookInstallation.objects.filter(
                    source_connection=conn,
                    repo_full_name=repo,
                    deleted_at__isnull=True,
                ).first()
                if existing is not None:
                    existing.webhook_url = target_url
                    existing.provider_short_circuited = True
                    existing.save(
                        update_fields=[
                            "webhook_url",
                            "provider_short_circuited",
                            "updated_at",
                            "version",
                        ]
                    )
                    return gql_success(webhook_install_to_type(existing))
                row = ScmWebhookInstallation.objects.create(
                    organization_id=org_id,
                    source_connection=conn,
                    repo_full_name=repo,
                    hook_id="",
                    webhook_url=target_url,
                    provider_short_circuited=True,
                )
            return gql_success(webhook_install_to_type(row))

        # PAT / OAuth-user path: resolve a webhook secret. Order:
        # 1) caller-supplied (no persistence — caller already shared).
        # 2) connection's stored ciphertext (we decrypt + re-use).
        # 3) mint a fresh one and persist on the connection.
        secret = (input.secret or "").strip()
        if not secret and conn.webhook_secret_backend_kind and conn.webhook_secret_ciphertext:
            try:
                plaintext = decrypt(
                    EncryptedSecret(
                        backend_kind=conn.webhook_secret_backend_kind,
                        backend_ref=bytes(conn.webhook_secret_ciphertext),
                    )
                )
                secret = plaintext.decode("utf-8")
            except Exception:
                secret = ""
        minted_new = False
        if not secret:
            import secrets as _secrets

            secret = _secrets.token_urlsafe(32)
            minted_new = True

        try:
            result = install_webhook(
                conn,
                repo_full_name=repo,
                target_url=target_url,
                secret=secret,
            )
        except ProviderError as exc:
            return gql_failure(
                exc.code,
                exc.message,
                field="connectionId" if exc.code == "AUTH_FAILED" else None,
            )

        with transaction.atomic():
            if minted_new:
                encrypted = encrypt_at_rest(secret.encode("utf-8"))
                conn.webhook_secret_backend_kind = encrypted.backend_kind
                conn.webhook_secret_ciphertext = encrypted.backend_ref
                conn.save(
                    update_fields=[
                        "webhook_secret_backend_kind",
                        "webhook_secret_ciphertext",
                        "updated_at",
                        "version",
                    ]
                )
            existing = ScmWebhookInstallation.objects.filter(
                source_connection=conn,
                repo_full_name=repo,
                deleted_at__isnull=True,
            ).first()
            if existing is not None:
                existing.hook_id = result.hook_id
                existing.webhook_url = result.webhook_url
                existing.provider_short_circuited = False
                existing.save(
                    update_fields=[
                        "hook_id",
                        "webhook_url",
                        "provider_short_circuited",
                        "updated_at",
                        "version",
                    ]
                )
                row = existing
            else:
                row = ScmWebhookInstallation.objects.create(
                    organization_id=org_id,
                    source_connection=conn,
                    repo_full_name=repo,
                    hook_id=result.hook_id,
                    webhook_url=result.webhook_url,
                    provider_short_circuited=False,
                )

        return gql_success(webhook_install_to_type(row))

    @strawberry.field
    @mutation_audit(action="scm.ssh_key.delete")
    @require_permission(Permission.SCM_KEY_DELETE)
    @tenant_scoped()
    def delete_ssh_deploy_key(
        self, info: Info, input: DeleteSshDeployKeyInput
    ) -> MutationResultType[SshDeployKeyType]:
        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no organization")
        row = SshDeployKey.objects.filter(
            guid=str(input.id), organization_id=org_id, deleted_at__isnull=True
        ).first()
        if row is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "key not found")
        row.soft_delete()
        return gql_success(ssh_key_to_type(row))

    @strawberry.field
    @mutation_audit(action="scm.push_ci_workflow")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def push_ci_workflow(
        self, info: Info, input: PushCiWorkflowInput
    ) -> MutationResultType[PushCiWorkflowResult]:
        """Reconcile the managed Astrolift CI workflow onto ``app``'s repo (#1212).

        DELEGATES to Phase 1's ``sync_workflow_file_to_repo`` (System B) — the
        one canonical renderer/pusher — so the onboarding wizard writes the SAME
        stamped ``astrolift-ci.yml`` (and shared ``.gitlab-ci.yml`` /
        ``bitbucket-pipelines.yml``) that autowire and the drift mutations do.
        This retires the legacy System-A ``astrolift-deploy.yml`` renderer,
        whose GitLab/Bitbucket output collided with System B's on the shared
        path — after consolidation each host emits exactly one managed file.

        ``connection_id`` (and the ``branch`` / ``commit_message`` / ``file_path``
        overrides) are retained for API compatibility but no longer consulted:
        System B resolves the org-level write connection, deploy branch, path and
        commit message itself. ``repo_url`` carries the review PR link when a
        protected deploy branch routed the change through a PR.
        """
        from astrolift_scm.services.workflow_sync import (
            WorkflowSyncError,
            _render_and_path,
            sync_workflow_file_to_repo,
        )

        tenant = get_current_tenant()
        org_id = tenant.organization_id if tenant else None
        if org_id is None:
            return gql_failure(ErrorCode.PRECONDITION.value, "no organization")

        app = (
            RegisteredApp.objects.filter(
                guid=str(input.app_id),
                organization_id=org_id,
                deleted_at__isnull=True,
            )
            .select_related("organization")
            .first()
        )
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appId")

        try:
            result = sync_workflow_file_to_repo(app)
        except WorkflowSyncError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, exc.message, field="appId")
        if result.status == "fetch_failed":
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                result.error or "couldn't sync CI workflow to repo",
            )

        # ``WorkflowSyncResult`` doesn't carry the path; re-derive the canonical
        # one for the (non-null) result field. ``_render_and_path`` is the single
        # source of truth for the per-host managed path.
        _body, file_path = _render_and_path(app)
        return gql_success(
            PushCiWorkflowResult(
                commit_sha=result.commit_sha,
                file_path=file_path,
                repo_url=result.pr_url,
            )
        )

    # ----------------------------------------------------------------
    # Managed CI-workflow drift actions (#1210, Phase 2)
    #
    # Three manual actions over the versioned-sync record, all gated
    # the same as ``push_ci_workflow`` (app.update, tenant-scoped) and
    # all returning the read-side ``AstroliftCiWorkflowSyncStatus`` so
    # the UI can re-render the drift badge from the mutation response:
    #
    #   * resync  — re-render + push the current template (fixes
    #               template_stale / absent). Reuses Phase 1's push.
    #   * adopt   — accept the repo's current file as the new baseline
    #               WITHOUT pushing (clears repo_drift / conflict).
    #   * refresh — recompute drift now from the repo (manual check).
    #
    # None of these autonomously write back to a repo except ``resync``,
    # which is an explicit operator-driven push (same call the Phase 1
    # "Sync workflow file" button makes).
    # ----------------------------------------------------------------

    @strawberry.field
    @mutation_audit(action="scm.ci_workflow.resync")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def resync_astrolift_ci_workflow(
        self, info: Info, input: CiWorkflowSyncActionInput
    ) -> MutationResultType[AppCiWorkflowSyncStatusType]:
        """Re-render + push the current template onto the repo (#1210).

        The manual "fix it now" for ``template_stale`` / ``absent``: reuses
        Phase 1's ``sync_workflow_file_to_repo`` (idempotent, protected-branch
        aware) which also re-stamps the sync record to ``in_sync``. Returns
        the refreshed drift status.
        """
        from astrolift_scm.services.workflow_sync import (
            WorkflowSyncError,
            sync_workflow_file_to_repo,
        )

        app = _ci_drift_app(input.app_id)
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appId")

        try:
            result = sync_workflow_file_to_repo(app)
        except WorkflowSyncError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, exc.message, field="appId")
        if result.status == "fetch_failed":
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                result.error or "couldn't sync CI workflow to repo",
            )

        app.refresh_from_db()
        return gql_success(build_ci_workflow_sync_status(app))

    @strawberry.field
    @mutation_audit(action="scm.ci_workflow.adopt")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def adopt_repo_ci_workflow(
        self, info: Info, input: CiWorkflowSyncActionInput
    ) -> MutationResultType[AppCiWorkflowSyncStatusType]:
        """Accept the repo's CURRENT workflow file as the new baseline (#1210).

        Clears a ``repo_drift`` / ``conflict`` by declaring the repo
        authoritative: re-points the sync record's digests + version at the
        repo file and flags ``in_sync`` — WITHOUT pushing anything. Per the
        epic caveat, this only re-baselines the stamp tracking; it does NOT
        import the repo file's config.
        """
        from astrolift_scm.services.ci_workflow_drift import (
            CiWorkflowAdoptError,
            CiWorkflowFetchError,
            adopt_repo_ci_workflow,
        )

        app = _ci_drift_app(input.app_id)
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appId")

        try:
            adopt_repo_ci_workflow(app)
        except CiWorkflowAdoptError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, exc.message, field="appId")
        except CiWorkflowFetchError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, f"{exc.code}: {exc.message}")

        app.refresh_from_db()
        return gql_success(build_ci_workflow_sync_status(app))

    @strawberry.field
    @mutation_audit(action="scm.ci_workflow.refresh")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def refresh_ci_workflow_sync_status(
        self, info: Info, input: CiWorkflowSyncActionInput
    ) -> MutationResultType[AppCiWorkflowSyncStatusType]:
        """Recompute drift now by reading the repo (#1210).

        The manual "check now" action: fetch the repo file, classify via the
        pure state machine, persist the result (+ ``checked_at``), and return
        the refreshed status. A transient rate limit is recorded as
        ``unknown`` (not a hard failure) so it never poisons the drift state
        into a false auth error.
        """
        from astrolift_scm.services.ci_workflow_drift import (
            CiWorkflowFetchError,
            evaluate_and_persist_sync_state,
        )

        app = _ci_drift_app(input.app_id)
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appId")

        try:
            evaluate_and_persist_sync_state(app)
        except CiWorkflowFetchError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, f"{exc.code}: {exc.message}")

        app.refresh_from_db()
        return gql_success(build_ci_workflow_sync_status(app))

    # ----------------------------------------------------------------
    # Fleet-wide managed-CI-workflow resync (#1211, Phase 3)
    #
    # The platform-admin "run the outbound sweep now" trigger. Unlike the
    # per-app Phase 2 actions above (org-scoped, ``app.update``), this is a
    # FLEET-WIDE operator action gated on ``admin.elevate`` — the dedicated
    # platform-admin grant (granted to no org role; superusers bypass in the
    # resolver) — so it is deliberately NOT ``@tenant_scoped``. It reuses the
    # same sweep the held ``CI_WORKFLOW_RESYNC`` schedule wraps.
    # ----------------------------------------------------------------

    @strawberry.field
    @mutation_audit(action="scm.ci_workflow.resync_all")
    @require_permission(Permission.ADMIN_ELEVATE)
    def resync_all_astrolift_ci_workflows(self, info: Info) -> MutationResultType[CiWorkflowResyncAllResult]:
        """Kick the outbound CI-workflow resync sweep across the WHOLE fleet.

        Platform-admin only (``admin.elevate``, fleet-wide — NOT a per-org
        permission). Runs the same sweep the held ``CI_WORKFLOW_RESYNC``
        schedule wraps, but on demand and inline so it works while the schedule
        is held: for every managed app it recomputes drift and auto-pushes ONLY
        the safe states (``template_stale`` / ``absent``), never clobbering
        ``repo_drift`` / ``conflict``. Bounded to ``RESYNC_ALL_INLINE_LIMIT``
        apps per call; returns counts by resulting state.
        """
        from astrolift_scm.services.ci_workflow_drift import sweep_ci_workflows

        summary = sweep_ci_workflows(limit=RESYNC_ALL_INLINE_LIMIT)
        return gql_success(
            CiWorkflowResyncAllResult(
                scanned=summary.scanned,
                pushed=summary.pushed,
                in_sync=summary.in_sync,
                repo_drift=summary.repo_drift,
                conflict=summary.conflict,
                unknown=summary.unknown,
                skipped=summary.skipped,
                failed=summary.failed,
            )
        )

    @strawberry.field
    @mutation_audit(action="scm.ci_workflow.reconcile_pr")
    @require_permission(Permission.APP_UPDATE)
    @tenant_scoped()
    def open_ci_workflow_reconcile_pr(
        self, info: Info, input: CiWorkflowSyncActionInput
    ) -> MutationResultType[AppCiWorkflowSyncStatusType]:
        """Open a reviewable PR overwriting a drifted file with the template (#1212).

        For an app in ``repo_drift`` / ``conflict`` — someone hand-edited the
        managed workflow file. Rather than clobbering their edits in place (what
        ``resync`` does on an unprotected branch), this renders the CURRENT
        template and **reuses Phase 1's side-branch PR machinery**
        (``sync_workflow_file_to_repo`` with ``force_pr=True``) so the operator
        diffs their edits against the template and merges deliberately. That
        machinery is idempotent — the side-branch name is deterministic and the
        host's "a PR already exists" is treated as success — so re-calling
        refreshes the existing PR instead of opening a duplicate.

        Precondition-fails (never 500) when there's nothing to reconcile: no
        source repo, no org connection, or the file is ``in_sync`` / ``absent``
        / otherwise not drifted. Bitbucket has no side-branch PR flow wired, so
        a reconcile there fails cleanly rather than direct-writing over edits.
        """
        from astrolift_scm.services.ci_workflow_drift import (
            CiWorkflowFetchError,
            SyncState,
            evaluate_and_persist_sync_state,
        )
        from astrolift_scm.services.workflow_sync import (
            WorkflowSyncError,
            sync_workflow_file_to_repo,
        )

        app = _ci_drift_app(input.app_id)
        if app is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "app not found", field="appId")

        # Authoritatively re-classify drift from the repo now (this also
        # refreshes the badge). Surfaces "no source repo" / "no org connection"
        # as a clean precondition rather than a later crash on the push path.
        try:
            state = evaluate_and_persist_sync_state(app)
        except CiWorkflowFetchError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, f"{exc.code}: {exc.message}", field="appId")

        if state not in (SyncState.REPO_DRIFT, SyncState.CONFLICT):
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                f"nothing to reconcile: the managed workflow is '{state.value}'. "
                "A reconcile PR is only for a drifted (repo_drift / conflict) file.",
                field="appId",
            )

        # Reuse the Phase 1 PR-opening machinery (branch create + commit + open
        # PR/MR), forced onto a side branch so the template overwrite lands as a
        # reviewable PR instead of clobbering the operator's edits in place.
        try:
            result = sync_workflow_file_to_repo(app, force_pr=True)
        except WorkflowSyncError as exc:
            return gql_failure(ErrorCode.PRECONDITION.value, exc.message, field="appId")
        if result.status == "fetch_failed":
            return gql_failure(
                ErrorCode.PRECONDITION.value,
                result.error or "couldn't open the reconcile PR",
            )

        app.refresh_from_db()
        return gql_success(build_ci_workflow_sync_status(app))
