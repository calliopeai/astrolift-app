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

Auth surface: pushing CI secrets is an autonomous *platform* write, so
it authenticates as the org **GitHub App installation** — resolved via
``connection_resolver.resolve_connection(purpose=PLATFORM_REPO_WRITE)``,
the same identity the webhook install / workflow dispatch / manifest
read use. It is NOT the viewer's personal OAuth token: that token is a
human's, expires silently, and mixing it into a platform write was the
source of the intermittent 401 on secret pushes. The human who clicked
the button is still recorded — but out-of-band, as the deploy-token
issuer/rotator (``by_user_id``), not by borrowing their GitHub identity.
If the org hasn't installed the App, resolution raises PRECONDITION.

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
from astrolift_scm.services.connection_resolver import (
    PLATFORM_REPO_WRITE,
    ConnectionResolutionError,
    resolve_connection,
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
# GitHub connection resolution
# ---------------------------------------------------------------------------
#
# The GitHub CI-secrets paths (push + validate) resolve the org GitHub
# App installation via ``connection_resolver`` — see the module
# docstring. There is deliberately no personal-GitHub picker here any
# more: authenticating a platform write as a human's OAuth token was the
# bug this module split away from. GitLab still uses a personal picker
# below because GitLab has no App-installation identity to separate onto.


def _github_auth_scheme(connection: SourceConnection) -> str:
    """The ``Authorization`` scheme for a GitHub connection's token.

    App-installation tokens go out as ``Bearer``; user/PAT tokens as
    ``token``. Mirrors ``services.workflows._github_auth_header`` so the
    secrets writes speak the same auth dialect as the other GitHub
    call sites."""
    return "Bearer" if connection.kind == "github_app_install" else "token"


def _pick_personal_gitlab_connection(*, organization_id: int, user_id: int) -> SourceConnection | None:
    """Return the viewer's active personal GitLab connection (OAuth-user
    or PAT) in the app's org. Same scoping rationale as the GitHub
    picker — the variables we PUT through this connection get
    attributed to the human in GitLab's audit log."""
    return (
        SourceConnection.objects.filter(
            organization_id=organization_id,
            user_id=user_id,
            kind__in=(
                SourceConnection.Kind.GITLAB_OAUTH_USER,
                SourceConnection.Kind.GITLAB_PAT,
            ),
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
    token_scheme: str = "token",
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
            "Authorization": f"{token_scheme} {token}",
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
    token_scheme: str = "token",
) -> None:
    """PUT /repos/{owner}/{repo}/actions/secrets/{name}.

    GitHub returns 201 on first create, 204 on update. Anything else
    bubbles as a ``GithubProviderError`` so the service can map it
    onto the ``PushSecretsResult`` envelope.

    Authenticating as the org App (the platform-write identity) means a
    403 here now signals the App is missing the "Secrets: write" repo
    permission, and a 404 signals the App isn't installed on this repo —
    distinct causes from the old stale-user-token 401.
    """
    url = f"{base}/repos/{_safe_repo(repo_full_name)}/actions/secrets/{urllib.parse.quote(name, safe='')}"
    body = {"encrypted_value": sealed_b64, "key_id": key_id}
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="PUT",
        headers={
            "Authorization": f"{token_scheme} {token}",
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
# Validation — read-only check that the five Actions secrets are present
# on the app's source repo (#693).
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class CiSecretValidation:
    """One row of the validate-CI-secrets report.

    ``is_set`` is True when GitHub's ``GET /repos/{owner}/{repo}/actions/
    secrets/{name}`` returned 200 — proving the secret is registered.

    ``is_current`` is a best-effort 'matches the platform's expected
    value' check.  GitHub's Actions secrets API never returns the
    plaintext (which is the whole point of sealed-box encryption), so
    'current' really means 'was last updated AFTER the platform's most
    recent push of CI secrets'.  Operators read it as 'looks fresh'
    rather than 'byte-equal'.  When the platform has never pushed
    secrets to this repo we return None to indicate 'unknown'.
    """

    secret_name: str
    is_set: bool
    is_current: bool | None = None
    updated_at: str = ""
    """ISO 8601 timestamp returned by GitHub on each secret row; the FE
    can render 'updated 2 days ago' next to the green check."""


@dataclasses.dataclass(frozen=True, slots=True)
class ValidateCiSecretsResult:
    """Outcome of a ``validate_astrolift_ci_secrets`` call.

    ``ok=False`` carries a non-empty ``error_code`` + ``error_message``;
    ``results`` is empty in that case.  ``ok=True`` always returns a
    full row per canonical secret name even when the secret is absent —
    the FE renders a red X for the missing rows.
    """

    ok: bool
    results: tuple[CiSecretValidation, ...] = ()
    error_code: str = ""
    error_message: str = ""


def _list_github_repo_secret_names(
    *,
    token: str,
    base: str,
    repo_full_name: str,
    token_scheme: str = "token",
) -> dict[str, str]:
    """GET /repos/{owner}/{repo}/actions/secrets — return a name → updated_at
    mapping for every Actions secret on the repo.

    GitHub paginates this endpoint; 30 per page by default.  In practice
    repos have <30 Actions secrets but the loop handles it cleanly via
    the Link header just in case.  Raises ``GithubProviderError`` on
    auth / network / 5xx so the caller can map onto the envelope.
    """
    out: dict[str, str] = {}
    next_url: str | None = f"{base}/repos/{_safe_repo(repo_full_name)}/actions/secrets?per_page=100"
    while next_url:
        req = urllib.request.Request(
            next_url,
            headers={
                "Authorization": f"{token_scheme} {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "astrolift",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                link_header = resp.headers.get("Link", "") or ""
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

        for entry in payload.get("secrets", []) or []:
            name = entry.get("name")
            updated_at = entry.get("updated_at") or ""
            if isinstance(name, str) and name:
                out[name] = updated_at if isinstance(updated_at, str) else ""

        # Follow rel="next" if present (GitHub Link header pagination).
        next_url = _parse_link_next(link_header)
    return out


def _parse_link_next(link_header: str) -> str | None:
    """Pull the ``rel="next"`` URL out of a GitHub-style ``Link`` header.

    Returns None when no next link is present.  Hand-rolled parser
    rather than pulling ``requests`` for one header — keeps the
    dependency surface flat."""
    if not link_header:
        return None
    for part in link_header.split(","):
        segment = part.strip()
        if 'rel="next"' not in segment:
            continue
        # Shape: <url>; rel="next"
        if segment.startswith("<"):
            end = segment.find(">")
            if end > 1:
                return segment[1:end]
    return None


def validate_astrolift_ci_secrets(
    app: RegisteredApp,
    *,
    viewer_user,
) -> ValidateCiSecretsResult:
    """Validate the five Astrolift CI secrets are set on the app's repo
    (#693).

    Read-only — never writes anything.  Auth is the org GitHub App
    installation (``purpose=PLATFORM_REPO_WRITE``), the same identity
    the push side uses, so validate reads exactly what the platform can
    write. ``viewer_user`` is accepted for signature parity with the
    push path but no longer selects the credential.

    For non-GitHub hosts the function returns ``UNSUPPORTED_SOURCE`` —
    GitLab / Bitbucket / Gitea each have their own variables API and
    each is its own follow-up.  The push-side has the same constraint;
    surfacing them as separate ungated error codes keeps the FE's
    error mapping simple.
    """
    del viewer_user  # credential is now the org App, not the viewer.

    if not app.source_repo:
        return ValidateCiSecretsResult(
            ok=False,
            error_code="NO_SOURCE_REPO",
            error_message="app has no source repo configured",
        )

    if app.source_kind != "github":
        return ValidateCiSecretsResult(
            ok=False,
            error_code="UNSUPPORTED_SOURCE",
            error_message=(
                f"CI secret validation is GitHub-only today; this app's "
                f"source_kind is {app.source_kind!r}.  Track per-host "
                f"validators as follow-up tickets."
            ),
        )

    try:
        connection = resolve_connection(
            app.organization_id,
            purpose=PLATFORM_REPO_WRITE,
            source_kind="github",
        )
    except ConnectionResolutionError as exc:
        return ValidateCiSecretsResult(
            ok=False,
            error_code=exc.code,
            error_message=exc.message,
        )

    base = _github_api_base(connection)
    try:
        token = _token(connection)
    except Exception as exc:  # noqa: BLE001 — pass through as recoverable
        return ValidateCiSecretsResult(
            ok=False,
            error_code="AUTH_FAILED",
            error_message=str(exc),
        )

    try:
        present = _list_github_repo_secret_names(
            token=token,
            base=base,
            repo_full_name=app.source_repo,
            token_scheme=_github_auth_scheme(connection),
        )
    except GithubProviderError as exc:
        return ValidateCiSecretsResult(
            ok=False,
            error_code=exc.code,
            error_message=exc.message,
        )

    # 'current' = secret's updated_at is newer than the app's most recent
    # CI-secrets push.  We track that via ``RegisteredApp.ci_secrets_pushed_at``
    # when present; absent we surface None per CiSecretValidation contract.
    pushed_at = getattr(app, "ci_secrets_pushed_at", None)
    out: list[CiSecretValidation] = []
    for name in _GITHUB_SECRET_NAMES:
        updated_at = present.get(name, "")
        is_set = bool(updated_at) or (name in present)
        is_current: bool | None
        if not is_set:
            is_current = False
        elif pushed_at is None or not updated_at:
            is_current = None
        else:
            # GitHub returns ISO-8601 strings; tolerate Z suffix.
            try:
                import datetime as _dt

                parsed = _dt.datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                is_current = parsed >= pushed_at
            except (ValueError, TypeError):
                is_current = None
        out.append(
            CiSecretValidation(
                secret_name=name,
                is_set=is_set,
                is_current=is_current,
                updated_at=updated_at,
            )
        )
    return ValidateCiSecretsResult(ok=True, results=tuple(out))


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

    Auth is the org GitHub App installation (``PLATFORM_REPO_WRITE``),
    not the viewer's personal token; the repo's public sealed-box key is
    fetched, each secret value is sealed under that key, and the
    encrypted bytes are PUT to the Actions secrets endpoint. The deploy
    token slot is rotated as part of the push (the old plaintext isn't
    recoverable) — callers see the new last-4 in the returned envelope.
    The clicking human is recorded out-of-band as the deploy-token
    issuer (``by_user_id``), preserving attribution without borrowing
    their GitHub identity for the write.

    Raises ``PushSecretsError`` for caller-fault conditions (no repo,
    unsupported source kind, org App not installed); returns a
    ``PushSecretsResult(ok=False, ...)`` for runtime failures against
    the host.
    """
    if not app.source_repo:
        raise PushSecretsError(
            "NO_SOURCE_REPO",
            "app has no source repo configured; can't push CI secrets",
        )

    if app.source_kind in {"bitbucket", "gitea"}:
        # Bitbucket Pipelines variables + Gitea Actions secrets each
        # need their own driver — non-trivial work, tracked as separate
        # follow-up issues. Raise NotImplementedError so the resolver
        # layer (mutations.py:2648) maps to PRECONDITION the same way
        # it did before.
        raise NotImplementedError(
            f"Pushing CI secrets is not supported for source_kind={app.source_kind!r} yet; "
            "see the per-host follow-ups for the missing driver work."
        )
    if app.source_kind == "git_url":
        # Bare git URLs don't have a CI surface to push to — there's no
        # host-side variables API at all. Surface as a clean
        # UNSUPPORTED_SOURCE rather than NotImplementedError so the FE
        # shows "configure your CI manually" instead of "not yet
        # implemented".
        raise PushSecretsError(
            "UNSUPPORTED_SOURCE",
            "bare git URLs have no CI variable API; configure your CI runner manually",
        )

    if app.source_kind == "gitlab":
        return _push_gitlab_ci_variables(
            app,
            viewer_user=viewer_user,
            platform_api_url=platform_api_url,
        )

    if app.source_kind != "github":
        raise PushSecretsError(
            "UNSUPPORTED_SOURCE",
            f"unsupported source_kind {app.source_kind!r} for CI-secret push",
        )

    # Attribution only: who clicked the button. Best-effort — the
    # deploy-token rotation records it as issuer/rotator, out-of-band
    # from the GitHub credential. A missing viewer doesn't block the
    # push (the mutation layer already gated on permission).
    viewer_id = getattr(viewer_user, "pk", None) or getattr(viewer_user, "id", None)
    if not getattr(viewer_user, "is_authenticated", False):
        viewer_id = None

    # The credential is the org App installation, not the human. If the
    # org hasn't installed the App, resolution raises PRECONDITION.
    try:
        connection = resolve_connection(
            app.organization_id,
            purpose=PLATFORM_REPO_WRITE,
            source_kind="github",
        )
    except ConnectionResolutionError as exc:
        raise PushSecretsError(exc.code, exc.message) from exc

    token_scheme = _github_auth_scheme(connection)

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
            token_scheme=token_scheme,
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
                token_scheme=token_scheme,
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


# ---------------------------------------------------------------------------
# GitLab branch
# ---------------------------------------------------------------------------


# GitLab masks at the variable level (vs GitHub's "all secrets are
# encrypted at rest"). The names match the GitHub ones so the workflow
# template can reference them identically — operator-friendly mental
# model + one renderer if/when we render the per-host workflow YAML.
_GITLAB_VARIABLE_NAMES = _GITHUB_SECRET_NAMES


def _push_gitlab_ci_variables(
    app: RegisteredApp,
    *,
    viewer_user,
    platform_api_url: str,
) -> PushSecretsResult:
    """Push the five Astrolift CI variables onto a GitLab project's
    CI/CD variables surface (#531).

    Shape parallels the GitHub branch — same five names, same deploy-
    token rotate-then-push ordering, same return envelope so callers
    don't need to switch on host. Differences from GitHub:

    - Auth: GitLab connections carry a bearer token directly; no sealed
      box (GitLab encrypts variables at rest on its side).
    - API: ``POST /projects/:id/variables`` to create, fallback to
      ``PUT /projects/:id/variables/:key`` on already-exists.
    - Masking: every variable is marked ``masked=True`` so it doesn't
      leak into CI logs. GitLab rejects values that don't pass its
      mask-eligibility regex — surfaces as ``SECRET_PUT_FAILED`` so
      the operator can fix the value upstream (e.g. an empty
      ``push_role_ref`` would fail the masked check).
    """
    from astrolift_scm.providers.gitlab import (
        GitlabProviderError,
        put_gitlab_project_variable,
    )

    viewer_id = getattr(viewer_user, "pk", None) or getattr(viewer_user, "id", None)
    if not viewer_id or not getattr(viewer_user, "is_authenticated", False):
        raise PushSecretsError(
            "NO_PERSONAL_CONNECTION",
            "Connect your GitLab account first (Account drawer → Connected accounts).",
        )

    connection = _pick_personal_gitlab_connection(
        organization_id=app.organization_id,
        user_id=viewer_id,
    )
    if connection is None:
        raise PushSecretsError(
            "NO_PERSONAL_CONNECTION",
            "Connect your GitLab account first (Account drawer → Connected accounts).",
        )

    # Mirror the GitHub flow: rotate the deploy token only after we've
    # proven we can speak to the host. We probe with a dry GET against
    # the variables endpoint via a 404 on a never-used key — if auth
    # fails we surface AUTH_FAILED here rather than after the rotate.
    try:
        from astrolift_scm.providers.gitlab import _api_base, _token

        token = _token(connection)
        # Lightweight reach check: HEAD against the project's variables
        # collection. We use the existing put helper's error mapping by
        # making a no-op POST with an invalid empty key — GitLab returns
        # 400 with a validation error on the body, but auth issues
        # surface as 401/403 first.
        import urllib.error
        import urllib.parse
        import urllib.request

        probe_url = (
            f"{_api_base(connection)}/api/v4/projects/"
            f"{urllib.parse.quote(app.source_repo, safe='')}/variables?per_page=1"
        )
        req = urllib.request.Request(
            probe_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "astrolift",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                _ = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return PushSecretsResult(
                    ok=False,
                    error_code="AUTH_FAILED",
                    error_message=f"GitLab rejected the token ({exc.code}). Reconnect or rotate.",
                )
            if exc.code == 404:
                return PushSecretsResult(
                    ok=False,
                    error_code="NOT_FOUND",
                    error_message=f"GitLab couldn't find project {app.source_repo}. Check project access.",
                )
            # Other 4xx/5xx on the probe — fall through to the writes;
            # individual variable writes will report their own errors.
        except urllib.error.URLError as exc:
            return PushSecretsResult(
                ok=False,
                error_code="NETWORK",
                error_message=f"Couldn't reach GitLab: {exc.reason}",
            )
    except GitlabProviderError as exc:
        return PushSecretsResult(
            ok=False,
            error_code=exc.code,
            error_message=exc.message,
        )

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

    for name in _GITLAB_VARIABLE_NAMES:
        try:
            put_gitlab_project_variable(
                connection,
                repo_full_name=app.source_repo,
                key=name,
                value=values[name],
                masked=True,
                protected=False,
            )
        except GitlabProviderError as exc:
            return PushSecretsResult(
                ok=False,
                error_code="SECRET_PUT_FAILED",
                error_message=f"{name}: {exc.message}",
            )

    return PushSecretsResult(
        ok=True,
        secret_names=_GITLAB_VARIABLE_NAMES,
        new_token_last_4=new_token_plaintext[-4:],
    )
