"""Push the five Astrolift CI secrets onto a source-host repository (#383).

The CI workflow shipped by ``ci-setup-section`` (#382) keys off five
named GitHub Actions secrets — ``ASTROLIFT_PUSH_ROLE_ARN``,
``ASTROLIFT_ECR_URI``, ``ASTROLIFT_APP_SLUG``, ``ASTROLIFT_API_URL``,
``ASTROLIFT_DEPLOY_TOKEN``. Operators previously had to copy each value
out of the platform and paste it into the GitHub UI; this service does
the round-trip for them.

The deploy-token slot is the one that doesn't fall out of the model:
its plaintext only exists at mint time and we can't fish it back out
of the hash. So this service *rotates* the app's deploy token as part
of the push — that produces fresh plaintext we can seal + PUT. The
rotation is *immediate* (no grace window) so the ``Push & rotate``
affordance's confirm-dialog copy reflects reality: the old token
stops working the moment GitHub accepts the new sealed value. In-
flight CI runs holding the previous token will need to be re-kicked.

The personal-OAuth connection is the auth surface (per #395): we use
the *viewer's* GitHub identity, not an org-level PAT, so the resulting
secrets are attributed to the human who clicked the button. The
operator must have completed the OAuth dance through their Account
drawer first; PRECONDITION otherwise.

GitHub is the only host that exposes a ``PUT secrets`` primitive
today. GitLab masked variables and Bitbucket Pipelines variables
both have similar shapes but they're not interchangeable wire-wise;
those raise ``NotImplementedError`` until each driver lands.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import urllib.error
import urllib.parse
import urllib.request

from astrolift_lifecycle.deploy_tokens import (
    DEFAULT_TTL_DAYS,
    issue_token,
    rotate_token,
)
from astrolift_lifecycle.models import DeployToken
from astrolift_registry.models import RegisteredApp
from astrolift_scm.models import SourceConnection
from astrolift_scm.providers.github import (
    GITHUB_API_DEFAULT,
    GithubProviderError,
    _token,
)

# Names the workflow YAML keys off. Order is the order the FE renders
# them; the resolver returns this list back to the caller so the toast
# can name each one. Keep in sync with ``ci-setup-section.tsx``.
_GITHUB_SECRET_NAMES = (
    "ASTROLIFT_PUSH_ROLE_ARN",
    "ASTROLIFT_ECR_URI",
    "ASTROLIFT_APP_SLUG",
    "ASTROLIFT_API_URL",
    "ASTROLIFT_DEPLOY_TOKEN",
)


@dataclasses.dataclass(frozen=True, slots=True)
class PushSecretsResult:
    """Outcome of a ``push_astrolift_ci_secrets`` call.

    ``ok=True``: every secret PUT returned 2xx and the deploy-token
    rotation persisted. ``secret_names`` is the canonical name list
    (caller renders it verbatim). ``new_token_last_4`` is the last
    four characters of the just-minted plaintext — the only piece of
    the new token the UI ever sees (full plaintext is sealed and
    handed to GitHub, never returned to the browser).

    ``ok=False``: ``error_code`` is one of:
      - ``NO_PERSONAL_CONNECTION``: viewer hasn't connected GitHub.
      - ``UNSUPPORTED_SOURCE``: app source kind isn't GitHub-shaped.
      - ``PUBLIC_KEY_FETCH``: couldn't fetch the repo's sealed-box key.
      - ``SECRET_PUT_FAILED``: at least one PUT didn't return 2xx.
      - ``AUTH_FAILED`` / ``NOT_FOUND`` / ``API_ERROR`` / ``NETWORK``:
        pass-through from the GitHub provider error envelope.
    """

    ok: bool
    secret_names: tuple[str, ...] = ()
    new_token_last_4: str = ""
    error_code: str = ""
    error_message: str = ""


class PushSecretsError(Exception):
    """Raised when the caller's invariants don't hold (no source repo,
    unsupported host, no personal connection). Runtime API failures
    are returned as ``PushSecretsResult(ok=False, ...)`` instead — only
    "should never happen at the call site" conditions raise."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Libsodium sealed box
# ---------------------------------------------------------------------------


def seal_secret_for_repo(public_key_b64: str, value: str) -> str:
    """Encrypt ``value`` for a GitHub repo's Actions-secrets public key.

    GitHub returns the repo's sealed-box public key as a base64-encoded
    Curve25519 32-byte key on ``/repos/{owner}/{repo}/actions/secrets/public-key``.
    The PUT payload's ``encrypted_value`` is the libsodium sealed box
    of the secret bytes under that key, base64-encoded. Sealed-box uses
    an ephemeral sender keypair, so the receiver (GitHub) is the only
    party that can decrypt — exactly what we want for write-only secret
    delivery.

    Pure-Python: ``pynacl`` wraps libsodium so we don't shell out.
    """
    # Import inside the function so the dependency-install gate is at
    # call time rather than module import — keeps unrelated tests in
    # ``astrolift_scm`` runnable on an environment that hasn't picked
    # up the Pipfile bump yet.
    from nacl.encoding import Base64Encoder
    from nacl.public import PublicKey, SealedBox

    public_key = PublicKey(public_key_b64.encode("ascii"), encoder=Base64Encoder)
    sealed = SealedBox(public_key).encrypt(value.encode("utf-8"))
    return base64.b64encode(sealed).decode("ascii")


# ---------------------------------------------------------------------------
# Personal-connection picker (#395)
# ---------------------------------------------------------------------------


def _pick_personal_github_connection(*, organization_id: int, user_id: int) -> SourceConnection | None:
    """Return the viewer's active personal GitHub OAuth connection in
    the app's org, or None when no usable row exists.

    The push-secrets path is intentionally user-scoped: the resulting
    secrets are attributed to the human who clicked the button on
    GitHub's audit log, not to an org-level PAT shared across the
    organization. We don't fall through to org-level connections —
    that's a deliberate choice (#395) so an operator who hasn't
    connected their personal account gets a clear PRECONDITION rather
    than a silent attribution swap.
    """
    return (
        SourceConnection.objects.filter(
            organization_id=organization_id,
            user_id=user_id,
            kind=SourceConnection.Kind.GITHUB_OAUTH_USER,
            is_active=True,
            is_orphaned=False,
            deleted_at__isnull=True,
        )
        .order_by("-updated_at", "-pk")
        .first()
    )


# ---------------------------------------------------------------------------
# GitHub Actions secrets API
# ---------------------------------------------------------------------------


def _github_api_base(connection: SourceConnection) -> str:
    return (connection.api_base_url or GITHUB_API_DEFAULT).rstrip("/")


def _safe_repo(repo_full_name: str) -> str:
    return "/".join(urllib.parse.quote(p, safe="") for p in repo_full_name.split("/", 1))


def _fetch_repo_public_key(
    *,
    token: str,
    base: str,
    repo_full_name: str,
) -> tuple[str, str]:
    """GET /repos/{owner}/{repo}/actions/secrets/public-key.

    Returns ``(key_b64, key_id)``. ``key_id`` is the opaque identifier
    GitHub expects back on each PUT — it ties the sealed bytes to the
    public key version, so a key rotation on GitHub's side surfaces as
    a clean 422 instead of silent corruption.
    """
    url = f"{base}/repos/{_safe_repo(repo_full_name)}/actions/secrets/public-key"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if exc.code in (401, 403):
            raise GithubProviderError(
                "AUTH_FAILED",
                f"GitHub rejected the token ({exc.code}). Reconnect or rotate.",
                recoverable=True,
            ) from exc
        if exc.code == 404:
            raise GithubProviderError(
                "NOT_FOUND",
                f"GitHub couldn't find {repo_full_name}. Check repo access.",
                recoverable=True,
            ) from exc
        raise GithubProviderError(
            "API_ERROR",
            f"GitHub returned {exc.code}: {body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GithubProviderError("NETWORK", f"Couldn't reach GitHub: {exc.reason}") from exc

    key = payload.get("key")
    key_id = payload.get("key_id")
    if not isinstance(key, str) or not isinstance(key_id, str):
        raise GithubProviderError(
            "UNEXPECTED_SHAPE",
            "GitHub public-key response missing 'key' or 'key_id'",
        )
    return key, key_id


def _put_repo_secret(
    *,
    token: str,
    base: str,
    repo_full_name: str,
    name: str,
    sealed_b64: str,
    key_id: str,
) -> None:
    """PUT /repos/{owner}/{repo}/actions/secrets/{name}.

    GitHub returns 201 on first create, 204 on update. Anything else
    bubbles as a ``GithubProviderError`` so the service can map it
    onto the ``PushSecretsResult`` envelope.
    """
    url = f"{base}/repos/{_safe_repo(repo_full_name)}/actions/secrets/{urllib.parse.quote(name, safe='')}"
    body = {"encrypted_value": sealed_b64, "key_id": key_id}
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="PUT",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "astrolift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            _ = resp.read()  # 201 or 204; body is empty.
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if exc.code in (401, 403):
            raise GithubProviderError(
                "AUTH_FAILED",
                f"GitHub rejected the token ({exc.code}) writing {name!r}. Reconnect or rotate.",
                recoverable=True,
            ) from exc
        if exc.code == 404:
            raise GithubProviderError(
                "NOT_FOUND",
                f"GitHub couldn't find {repo_full_name}. Check repo access.",
                recoverable=True,
            ) from exc
        raise GithubProviderError(
            "API_ERROR",
            f"GitHub returned {exc.code} writing {name!r}: {body_text}",
        ) from exc
    except urllib.error.URLError as exc:
        raise GithubProviderError(
            "NETWORK",
            f"Couldn't reach GitHub writing {name!r}: {exc.reason}",
        ) from exc


# ---------------------------------------------------------------------------
# Deploy-token rotation hook
# ---------------------------------------------------------------------------


def _rotate_or_issue_deploy_token(app: RegisteredApp, *, by_user_id: int | None) -> str:
    """Pick the app's freshest active deploy token and rotate it with
    an immediate cutover; if no rows exist, mint a brand-new one
    named for the CI push.

    Returns the new plaintext. The ``Push & rotate`` affordance is an
    explicit, operator-initiated rotation: the dialog promises that
    the old token stops working immediately, and that's the safer
    security posture for an action labeled "rotate now". The
    grace-window path on ``rotate_token`` (default 1h) remains the
    right default for *implicit* rotations triggered elsewhere — the
    grace exists to keep in-flight CI alive during an operator-
    transparent rotation. Here the operator IS rotating on purpose
    and has been warned, so we skip grace and revoke the old hash
    in the same transaction.
    """
    token = (
        DeployToken.objects.filter(
            registered_app=app,
            deleted_at__isnull=True,
            is_revoked=False,
        )
        .order_by("-created_at", "-pk")
        .first()
    )
    if token is not None:
        _row, plaintext = rotate_token(token, immediate=True)
        return plaintext
    _row, plaintext = issue_token(
        app=app,
        name="ci-push-rotated",
        scopes=["app.deploy"],
        expires_in_days=DEFAULT_TTL_DAYS,
        by_user_id=by_user_id,
    )
    return plaintext


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def push_astrolift_ci_secrets(
    app: RegisteredApp,
    *,
    viewer_user,
    platform_api_url: str,
) -> PushSecretsResult:
    """Push the five Astrolift CI secrets to ``app``'s source repo.

    Auth is the viewer's personal GitHub OAuth connection in the app's
    org; the repo's public sealed-box key is fetched, each secret value
    is sealed under that key, and the encrypted bytes are PUT to the
    Actions secrets endpoint. The deploy token slot is rotated as part
    of the push (the old plaintext isn't recoverable) — callers see the
    new last-4 in the returned envelope.

    Raises ``PushSecretsError`` for caller-fault conditions (no repo,
    unsupported source kind, no personal connection); returns a
    ``PushSecretsResult(ok=False, ...)`` for runtime failures against
    the host.
    """
    if not app.source_repo:
        raise PushSecretsError(
            "NO_SOURCE_REPO",
            "app has no source repo configured; can't push CI secrets",
        )

    if app.source_kind in {"gitlab", "bitbucket", "gitea", "git_url"}:
        raise NotImplementedError(
            "Pushing CI secrets is GitHub-only for now; " "configure GitLab/Bitbucket variables manually."
        )
    if app.source_kind != "github":
        raise PushSecretsError(
            "UNSUPPORTED_SOURCE",
            f"unsupported source_kind {app.source_kind!r} for CI-secret push",
        )

    viewer_id = getattr(viewer_user, "pk", None) or getattr(viewer_user, "id", None)
    if not viewer_id or not getattr(viewer_user, "is_authenticated", False):
        raise PushSecretsError(
            "NO_PERSONAL_CONNECTION",
            ("Connect your GitHub account first " "(Account drawer → Connected accounts)."),
        )

    connection = _pick_personal_github_connection(
        organization_id=app.organization_id,
        user_id=viewer_id,
    )
    if connection is None:
        raise PushSecretsError(
            "NO_PERSONAL_CONNECTION",
            ("Connect your GitHub account first " "(Account drawer → Connected accounts)."),
        )

    try:
        token = _token(connection)
    except GithubProviderError as exc:
        return PushSecretsResult(
            ok=False,
            error_code=exc.code,
            error_message=exc.message,
        )

    base = _github_api_base(connection)

    try:
        public_key_b64, key_id = _fetch_repo_public_key(
            token=token,
            base=base,
            repo_full_name=app.source_repo,
        )
    except GithubProviderError as exc:
        return PushSecretsResult(
            ok=False,
            error_code="PUBLIC_KEY_FETCH",
            error_message=exc.message,
        )

    # Rotate (or mint) the deploy token *after* we've proven we can
    # reach GitHub — otherwise a credential failure on the host would
    # rotate the token without ever delivering it, breaking live CI.
    new_token_plaintext = _rotate_or_issue_deploy_token(
        app,
        by_user_id=viewer_id,
    )

    values = {
        "ASTROLIFT_PUSH_ROLE_ARN": app.push_role_ref or "",
        "ASTROLIFT_ECR_URI": app.registry_repo_uri or "",
        "ASTROLIFT_APP_SLUG": app.slug,
        "ASTROLIFT_API_URL": (platform_api_url or "").rstrip("/"),
        "ASTROLIFT_DEPLOY_TOKEN": new_token_plaintext,
    }

    for name in _GITHUB_SECRET_NAMES:
        sealed = seal_secret_for_repo(public_key_b64, values[name])
        try:
            _put_repo_secret(
                token=token,
                base=base,
                repo_full_name=app.source_repo,
                name=name,
                sealed_b64=sealed,
                key_id=key_id,
            )
        except GithubProviderError as exc:
            return PushSecretsResult(
                ok=False,
                error_code="SECRET_PUT_FAILED",
                error_message=exc.message,
            )

    return PushSecretsResult(
        ok=True,
        secret_names=_GITHUB_SECRET_NAMES,
        new_token_last_4=new_token_plaintext[-4:],
    )
