"""
SourceConnection — per-org credential to a source-code host.

One row per (organization, kind, account_login) combination. The
operator can have several connections active at once (e.g. one
GitHub OAuth user-token AND a separate GitHub-App installation), so
the choice of "which connection to use" is per-app at registration
time.

The credential bytes are stored encrypted via ``core.secrets``
(default ``local_fernet``; future cloud-KMS backends carry an opaque
ref instead). The ``backend_kind`` column tells the migration
command which backend produced the ciphertext.

For GitHub specifically, ``repo_visibility_scopes`` constrains what
the connection is allowed to surface when the repo-listing query
later calls the GitHub API:

  - ``private_org``: private repos owned by the connected GitHub org
  - ``public_org``: public repos owned by the connected GitHub org
  - ``user_repos``: repos owned by the connected user account
  - ``public_non_org``: arbitrary public repos the user can see

The constraint is enforced at the SCM-API call site (phase 2); this
field is the policy the resolver will read.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class SourceConnection(BaseCoreModel):
    class Kind(models.TextChoices):
        GITHUB_OAUTH_APP = "github_oauth_app"
        # Per-user token issued by the OAuth dance against an
        # operator-registered GITHUB_OAUTH_APP row. The user FK is
        # set; the row's auth header is "token <token>".
        GITHUB_OAUTH_USER = "github_oauth_user"
        GITHUB_APP_INSTALL = "github_app_install"
        GITHUB_PAT = "github_pat"
        GITLAB_OAUTH_APP = "gitlab_oauth_app"
        GITLAB_OAUTH_USER = "gitlab_oauth_user"
        GITLAB_PAT = "gitlab_pat"
        BITBUCKET_OAUTH_APP = "bitbucket_oauth_app"
        BITBUCKET_OAUTH_USER = "bitbucket_oauth_user"
        BITBUCKET_PAT = "bitbucket_pat"
        GITEA_OAUTH_APP = "gitea_oauth_app"
        GITEA_OAUTH_USER = "gitea_oauth_user"
        GITEA_PAT = "gitea_pat"

    class VisibilityScope(models.TextChoices):
        PRIVATE_ORG = "private_org"
        PUBLIC_ORG = "public_org"
        USER_REPOS = "user_repos"
        PUBLIC_NON_ORG = "public_non_org"

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="source_connections",
        on_delete=models.CASCADE,
    )
    # NULL → org-level credential (PAT shared by everyone in the org,
    # GitHub-App installation, OAuth-app config row). Set → personal
    # credential created by an OAuth dance for *this user*.
    # Per-user rows are scoped to the user *and* the org they were in
    # when they connected; deactivating an org membership doesn't
    # automatically nuke the token (that's a separate operator action),
    # but a user delete cascades.
    user = models.ForeignKey(
        "auth.User",
        related_name="source_connections",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    # When this row is a per-user token, it points back to the
    # GITHUB_OAUTH_APP row whose client credentials produced it. The
    # backend uses this to find refresh creds and to know which OAuth
    # app config to revoke against if the user disconnects.
    parent_oauth_app = models.ForeignKey(
        "self",
        related_name="user_tokens",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    display_name = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="Operator-friendly label shown in the UI.",
    )

    # Identity on the SCM side. For OAuth: the user/org login of the
    # account that did the OAuth dance. For GitHub-App installs: the
    # owner login + installation_id. For PATs: the owner the PAT
    # belongs to.
    account_login = models.CharField(max_length=200, blank=True, default="")
    installation_id = models.CharField(max_length=64, blank=True, default="")

    # When the upstream removes/uninstalls this connection (GitHub
    # App installation.deleted, OAuth revocation, PAT revoked) we
    # flip ``is_orphaned`` so the deploy + token-mint paths refuse
    # to use this row. Reconnect flow clears it. Per spec 06 §4.23.
    is_orphaned = models.BooleanField(default=False)
    orphaned_at = models.DateTimeField(null=True, blank=True)
    orphaned_reason = models.CharField(max_length=255, blank=True, default="")
    # Softer signal than ``is_orphaned``: the token is still
    # technically present (or was, until very recently) but the
    # platform observed a 401 / token-expired response and wants the
    # user to walk back through the OAuth dance. The UI renders this
    # as an amber "Re-authorize" chip rather than the red "Reconnect
    # needed" state. Set by token-using call sites when they see a
    # recoverable auth failure; cleared on a successful OAuth dance.
    reauth_required = models.BooleanField(default=False)

    # Self-hosted Gitea / on-prem GitLab need a custom base URL;
    # github.com / gitlab.com are inferred from kind when blank.
    api_base_url = models.URLField(blank=True, default="")

    # The credential. Encrypted via core.secrets; backend_kind tells
    # the migration command which backend produced the bytes so we
    # can re-encrypt cleanly when an install moves to a cloud KMS.
    secret_backend_kind = models.CharField(max_length=32, default="local_fernet")
    secret_ciphertext = models.BinaryField(blank=True, default=b"")

    # OAuth-app config when this row IS the OAuth app (kind ends in
    # _oauth_app). client_id is non-secret; the client secret goes
    # in secret_ciphertext above.
    #
    # DUAL-SEMANTICS WARNING:
    #   For ``github_app_install`` rows this column carries the numeric
    #   **App ID** (e.g. ``3705068``) — the value GitHub embeds in
    #   webhook payloads + the value the legacy JWT ``iss`` claim used
    #   before GitHub recommended switching to the Client ID. The
    #   user-to-server OAuth ``client_id`` (e.g. ``Iv23lic8662KXwe4XKEI``)
    #   lives in ``app_client_id`` below. TODO: rename this column to
    #   ``provider_app_id`` in a follow-up migration so the dual use is
    #   no longer load-bearing on the column name.
    oauth_client_id = models.CharField(
        max_length=256,
        blank=True,
        default="",
        help_text=(
            "Non-secret provider identifier. For github_oauth_app + "
            "gitlab_oauth_app: the OAuth Client ID. For "
            "github_app_install: the numeric App ID (NOT the Client "
            "ID — see app_client_id for the OAuth Client ID)."
        ),
    )
    # GitHub App OAuth Client ID — string slug (e.g. ``Iv23lic8662KXwe4XKEI``
    # for new Apps; 20-char lowercase hex for legacy OAuth Apps). Required
    # for the OAuth dance URL (``/login/oauth/authorize?client_id=…``) and
    # preferred for the GitHub-App JWT ``iss`` claim per
    # https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app.
    # Distinct from ``oauth_client_id`` which on github_app_install rows
    # stores the numeric App ID for webhook-signature lookups + legacy
    # JWT compatibility. Empty on rows that pre-date this column.
    app_client_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=(
            "GitHub App OAuth Client ID (e.g. Iv23lic8662KXwe4XKEI). "
            "Required for github_oauth_app + github_app_install kinds. "
            "Not derivable from existing data; operator enters it once."
        ),
    )
    oauth_redirect_uri = models.CharField(max_length=512, blank=True, default="")

    # User-to-server OAuth client_secret for GitHub-App rows. The App's
    # manifest exchange returns this alongside the PEM; the PEM is the
    # installation-token signing key (goes in secret_ciphertext above)
    # and this is the per-user OAuth dance secret. Separate column so
    # the App row can do both things at once without column re-use.
    # Blank-by-default; only github_app_install rows populate it.
    oauth_client_secret_backend_kind = models.CharField(max_length=32, blank=True, default="")
    oauth_client_secret_ciphertext = models.BinaryField(blank=True, default=b"")

    # Visibility scope = what the resolver is allowed to surface
    # when listing repos through this connection.
    repo_visibility_scopes = models.JSONField(default=list, blank=True)

    # Token expiry (when the SCM gives us one). Refresh logic lives
    # in the worker; the resolver just refuses to use an expired
    # token and surfaces "reconnect" in the UI.
    token_expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    # Per-connection webhook secret. Generated on demand; the operator
    # copies the plaintext into the SCM host's webhook config exactly
    # once (we show it once and store the ciphertext). The receiver
    # HMAC-verifies inbound push payloads against this value before
    # firing DeployAppWorkflow.
    webhook_secret_backend_kind = models.CharField(max_length=32, blank=True, default="")
    webhook_secret_ciphertext = models.BinaryField(blank=True, default=b"")
    webhook_last_received_at = models.DateTimeField(null=True, blank=True)

    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "kind", "account_login"],
                condition=models.Q(deleted_at__isnull=True),
                name="scm_conn_unique_per_org_kind_login",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "is_active"]),
        ]

    @property
    def name(self) -> str:
        return self.display_name or f"{self.kind}:{self.account_login or '—'}"

    @property
    def is_oauth_app_config(self) -> bool:
        """True when this row holds OAuth-app credentials (not a token)."""
        return self.kind.endswith("_oauth_app") and not self.account_login

    @property
    def needs_client_id(self) -> bool:
        """True when this row REQUIRES ``app_client_id`` for the OAuth
        flow but it's empty — surfaces a "configuration incomplete"
        banner in the FE. Per-user token rows + non-GitHub rows are
        excluded; only the org-level GitHub config rows that drive the
        OAuth dance need this."""
        if not self.kind.startswith("github_"):
            return False
        if self.kind not in ("github_oauth_app", "github_app_install"):
            return False
        # Per-user token rows attach to a parent_oauth_app; they
        # themselves don't drive the OAuth dance.
        if self.user_id is not None:
            return False
        return not self.app_client_id
