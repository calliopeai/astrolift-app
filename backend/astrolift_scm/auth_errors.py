"""Shared builder for GitHub auth-failure messages (401 vs 403).

A 401 and a 403 from GitHub mean different things and need different
operator guidance, but the SCM call sites historically collapsed both
into one "reconnect or rotate" string. That guidance is right for a
401 (the credential is invalid or expired, so reconnecting mints a
fresh one) but actively wrong for a 403 on a GitHub App installation:
the App is connected and its token is valid; it simply lacks a
repository permission. Reconnecting re-installs the same
under-permissioned App and the 403 comes straight back. The real fix
is an org admin granting the App the missing permission and
re-approving the installation, because the permission lives on the
App, not on the token.

This helper builds the right message from the HTTP status, the
connection kind, and (when the caller knows them) the operation being
attempted plus the GitHub permission that would unblock it. Callers
keep raising the same error ``code`` (e.g. ``AUTH_FAILED``); only the
human-readable message changes.
"""

from __future__ import annotations

# ``SourceConnection.kind`` for an installed GitHub App. The 403 branch
# hinges on this: only an App installation carries a permission the
# operator can neither see nor fix by reconnecting.
GITHUB_APP_INSTALL_KIND = "github_app_install"


def github_auth_error_message(
    status_code: int,
    connection_kind: str,
    *,
    operation: str | None = None,
    permission: str | None = None,
) -> str:
    """Return operator-facing guidance for a GitHub 401 or 403.

    ``status_code`` is the HTTP status GitHub returned (401 or 403; any
    non-401 is treated as the permission case). ``connection_kind`` is
    ``SourceConnection.kind`` (e.g. ``github_app_install``,
    ``github_pat``, ``github_oauth_user``). ``operation`` is a short
    verb phrase for what was being attempted ("write Actions secrets");
    it defaults to a generic phrase when the caller has no specific one.
    ``permission`` is the GitHub permission that would unblock the call
    ("Secrets: write"); it is named verbatim when known.

    401 is always "the credential is invalid or expired, reconnect".
    403 splits on connection kind: an App installation needs an org
    admin to grant a permission and re-approve (reconnecting won't
    help, the permission lives on the App); a user token (PAT / OAuth)
    needs to be reconnected with the right scope.
    """
    op = operation or "perform this operation"

    if status_code == 401:
        return (
            "GitHub rejected the credentials (401). The token is invalid "
            "or expired. Reconnect the GitHub connection."
        )

    # 403: the credential is valid but lacks permission for ``op``.
    if connection_kind == GITHUB_APP_INSTALL_KIND:
        grant = f"the '{permission}'" if permission else "the required"
        return (
            f"GitHub returned 403. The App is installed but not permitted to "
            f"{op}. An org admin must grant the GitHub App {grant} permission "
            f"and re-approve the installation; reconnecting won't help, the "
            f"permission lives on the App."
        )

    scope = f"the '{permission}'" if permission else "the required"
    return (
        f"GitHub returned 403. The connected token lacks the scope to {op}. "
        f"Reconnect with a token that has {scope} permission."
    )
