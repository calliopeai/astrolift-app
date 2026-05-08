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
        GITHUB_APP_INSTALL = "github_app_install"
        GITHUB_PAT = "github_pat"
        GITLAB_OAUTH_APP = "gitlab_oauth_app"
        GITLAB_PAT = "gitlab_pat"
        BITBUCKET_OAUTH_APP = "bitbucket_oauth_app"
        BITBUCKET_PAT = "bitbucket_pat"
        GITEA_OAUTH_APP = "gitea_oauth_app"
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
    kind = models.CharField(max_length=32, choices=Kind.choices)
    display_name = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Operator-friendly label shown in the UI.",
    )

    # Identity on the SCM side. For OAuth: the user/org login of the
    # account that did the OAuth dance. For GitHub-App installs: the
    # owner login + installation_id. For PATs: the owner the PAT
    # belongs to.
    account_login = models.CharField(max_length=200, blank=True, default="")
    installation_id = models.CharField(max_length=64, blank=True, default="")

    # Self-hosted Gitea / on-prem GitLab need a custom base URL;
    # github.com / gitlab.com are inferred from kind when blank.
    api_base_url = models.URLField(blank=True, default="")

    # The credential. Encrypted via core.secrets; backend_kind tells
    # the migration command which backend produced the bytes so we
    # can re-encrypt cleanly when an install moves to a cloud KMS.
    secret_backend_kind = models.CharField(
        max_length=32, default="local_fernet"
    )
    secret_ciphertext = models.BinaryField(blank=True, default=b"")

    # OAuth-app config when this row IS the OAuth app (kind ends in
    # _oauth_app). client_id is non-secret; the client secret goes
    # in secret_ciphertext above.
    oauth_client_id = models.CharField(max_length=256, blank=True, default="")
    oauth_redirect_uri = models.CharField(max_length=512, blank=True, default="")

    # Visibility scope = what the resolver is allowed to surface
    # when listing repos through this connection.
    repo_visibility_scopes = models.JSONField(default=list, blank=True)

    # Token expiry (when the SCM gives us one). Refresh logic lives
    # in the worker; the resolver just refuses to use an expired
    # token and surfaces "reconnect" in the UI.
    token_expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

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
