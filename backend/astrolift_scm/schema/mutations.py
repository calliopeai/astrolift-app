"""SCM mutations: connect a host, paste a PAT, generate an SSH key."""

from __future__ import annotations

import strawberry
from django.db import transaction
from strawberry.types import Info

from astrolift_graphql import GUID, MutationResultType
from astrolift_graphql import failure as gql_failure
from astrolift_graphql import success as gql_success
from astrolift_identity.models import Organization
from astrolift_registry.models import RegisteredApp
from astrolift_scm.keygen import generate_ed25519_keypair
from astrolift_scm.models import SourceConnection, SshDeployKey
from astrolift_scm.schema.types import (
    SourceConnectionType,
    SshDeployKeyCreatedType,
    SshDeployKeyType,
    source_connection_to_type,
    ssh_key_to_type,
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
    oauth_redirect_uri: str | None = None
    repo_visibility_scopes: list[str] | None = None


@strawberry.input
class UpdateSourceConnectionInput:
    id: GUID
    display_name: str | None = None
    repo_visibility_scopes: list[str] | None = None
    is_active: bool | None = None
    rotate_secret_plaintext: str | None = None


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
    return None


# ---------------------------------------------------------------------------
# Root mutation
# ---------------------------------------------------------------------------


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
                oauth_redirect_uri=input.oauth_redirect_uri or "",
                repo_visibility_scopes=list(input.repo_visibility_scopes or []),
                secret_backend_kind=encrypted.backend_kind,
                secret_ciphertext=encrypted.backend_ref,
                is_active=True,
            )
        return gql_success(source_connection_to_type(conn))

    @strawberry.field
    @mutation_audit(action="scm.update")
    @require_permission(Permission.SCM_CONNECT)
    @tenant_scoped()
    def update_source_connection(
        self, info: Info, input: UpdateSourceConnectionInput
    ) -> MutationResultType[SourceConnectionType]:
        conn = SourceConnection.objects.filter(guid=str(input.id), deleted_at__isnull=True).first()
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
        conn.save()
        return gql_success(source_connection_to_type(conn))

    @strawberry.field
    @mutation_audit(action="scm.disconnect")
    @require_permission(Permission.SCM_DISCONNECT)
    @tenant_scoped()
    def disconnect_source(
        self, info: Info, input: DisconnectSourceInput
    ) -> MutationResultType[SourceConnectionType]:
        conn = SourceConnection.objects.filter(guid=str(input.id), deleted_at__isnull=True).first()
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

        conn = SourceConnection.objects.filter(guid=str(input.connection_id), deleted_at__isnull=True).first()
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
    @mutation_audit(action="scm.ssh_key.delete")
    @require_permission(Permission.SCM_KEY_DELETE)
    @tenant_scoped()
    def delete_ssh_deploy_key(
        self, info: Info, input: DeleteSshDeployKeyInput
    ) -> MutationResultType[SshDeployKeyType]:
        row = SshDeployKey.objects.filter(guid=str(input.id), deleted_at__isnull=True).first()
        if row is None:
            return gql_failure(ErrorCode.NOT_FOUND.value, "key not found")
        row.soft_delete()
        return gql_success(ssh_key_to_type(row))
