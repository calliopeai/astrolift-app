"""DB -> TOML write-back for cluster ingress auth config (#853).

When an operator toggles a cluster's ALB Cognito auth gate in the
Astrolift UI, ``updateTenantCluster`` persists the new
``TenantCluster.alb_auth_config`` and ``reconcileClusterIngresses``
(#851) patches the live Ingresses immediately. That keeps the running
cluster correct, but the *source of truth* — each app's
``astrolift.toml`` in its git repo — still reflects the old intent. A
fresh re-register of the cluster re-reads the repo and can silently
reset the auth gate.

This module closes the GitOps round-trip. For every app bound to the
cluster (via an :class:`AppEnvironment`), it fetches the app's
``astrolift.toml``, updates the ``[ingress.auth]`` section to match the
DB, and commits the change back to the app's repo:

    [ingress]
    class = "alb"

    [ingress.auth]
    kind = "cognito"
    user_pool_arn = "arn:aws:cognito-idp:..."
    user_pool_client_id = "..."
    user_pool_domain = "..."

When the auth gate is disabled (``alb_auth_config`` is ``None`` / empty)
the ``[ingress.auth]`` section is removed entirely; the surrounding
``[ingress]`` table (and the rest of the manifest) is preserved.

The TOML round-trips through ``tomllib`` (parse) -> ``tomli_w`` (dump),
same trade-off as :mod:`astrolift_manifest.env_edit`: comments and
hand-formatting are not preserved, which is acceptable because the
manifest is UI-generated and dev-side edits land through PRs.

The commit lands directly on the app's deploy branch (the branch the
deploy workflow reads). If the host refuses the push because the branch
is protected, the write-back falls back to a fresh feature branch + an
auto-PR so the operator's intent is captured without bypassing branch
protection (per #853).

Reuses ``astrolift_registry.services.manifest_sync._pick_source_connection``
to resolve the app's :class:`SourceConnection` and the
``astrolift_scm.providers`` dispatch (``fetch_file`` / ``put_file`` /
``open_pull_request``) for the host round-trip. All three SCM calls are
injectable so tests drive every branch without hitting the network.

Write-back is best-effort: the calling mutation wraps it so a failure
here can never block the UI save.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable, Mapping
from typing import Any

from astrolift_registry.models import RegisteredApp
from astrolift_scm.models import SourceConnection

log = logging.getLogger(__name__)

# Source kinds we can write back to. ``git_url`` / ``direct_upload`` apps
# have no host API to commit through, so they're skipped silently — the
# same "operator-managed apps can't have their toml updated" carve-out
# the issue calls out.
_WRITEABLE_SOURCE_KINDS = frozenset({"github", "gitlab", "bitbucket", "gitea"})

# The three Cognito fields the ALB auth gate is configured from. They
# live on ``TenantCluster.alb_auth_config`` and are mirrored verbatim
# into ``[ingress.auth]``; the renderer + ``ingress_reconcile`` read the
# same three keys, so this is the canonical set.
_COGNITO_FIELDS = ("user_pool_arn", "user_pool_client_id", "user_pool_domain")

# The central-auth fields mirrored into ``[ingress.auth]`` on nginx-family
# clusters. ``cookie_secret`` is deliberately absent: this table is
# committed to the app's own git repo, and the cookie secret signs the
# oauth2-proxy session. The manifest records where the gate points, never
# what would let someone mint a session against it.
_OIDC_FIELDS = ("auth_proxy_host", "discovery_url", "client_id")

_FetchFn = Callable[[SourceConnection, str, str, str], "str | None"]
_PutFn = Callable[..., Any]
_OpenPrFn = Callable[..., Any]


@dataclasses.dataclass(slots=True)
class WriteBackResult:
    """Outcome of a single-app write-back.

    ``status`` is one of:

    - ``committed``     — the manifest changed and a commit landed
                           (directly on the deploy branch, or via a PR
                           when the branch was protected).
    - ``unchanged``     — the repo's ``[ingress.auth]`` already matched
                           the DB; nothing was written (no empty commit).
    - ``skipped``       — the app can't be written back (no source repo,
                           non-git source kind, or no usable source
                           connection). Graceful no-op.
    - ``failed``        — the SCM round-trip raised. ``error`` carries
                           the host-side message. The caller logs it; it
                           never propagates to the UI save.

    ``via_pull_request`` is True when the commit landed on a fresh
    feature branch behind a PR because the deploy branch was protected.
    ``pull_request_url`` carries the operator-clickable PR URL in that
    case. ``commit_branch`` is the branch the change landed on.
    """

    status: str
    app_slug: str
    error: str | None = None
    via_pull_request: bool = False
    pull_request_url: str = ""
    commit_branch: str = ""

    @property
    def changed(self) -> bool:
        return self.status == "committed"


@dataclasses.dataclass(slots=True)
class ClusterWriteBackSummary:
    """Aggregate of write-back across every app bound to a cluster.

    ``repos_updated`` counts apps whose repo actually received a commit
    (``committed``); ``skipped`` and ``failed`` count the rest.
    ``results`` keeps the per-app detail for logging / future UI surfacing.
    """

    repos_updated: int = 0
    skipped: int = 0
    failed: int = 0
    results: list[WriteBackResult] = dataclasses.field(default_factory=list)

    def record(self, result: WriteBackResult) -> None:
        self.results.append(result)
        if result.status == "committed":
            self.repos_updated += 1
        elif result.status == "failed":
            self.failed += 1
        else:
            # ``skipped`` and ``unchanged`` both mean "no repo write".
            self.skipped += 1


def _default_fetch(
    connection: SourceConnection,
    repo_full_name: str,
    path: str,
    ref: str,
) -> str | None:
    from astrolift_scm.providers import fetch_file

    return fetch_file(connection, repo_full_name=repo_full_name, path=path, ref=ref)


def _default_put(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    path: str,
    branch: str,
    content: str,
    commit_message: str,
) -> Any:
    from astrolift_scm.providers import put_file

    return put_file(
        connection,
        repo_full_name=repo_full_name,
        path=path,
        branch=branch,
        content=content,
        commit_message=commit_message,
    )


def _default_open_pr(
    connection: SourceConnection,
    *,
    repo_full_name: str,
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
) -> Any:
    from astrolift_scm.providers import open_pull_request

    return open_pull_request(
        connection,
        repo_full_name=repo_full_name,
        head_branch=head_branch,
        base_branch=base_branch,
        title=title,
        body=body,
    )


def ingress_auth_section_from_db(
    alb_auth_config: Mapping[str, Any] | None,
) -> dict[str, str] | None:
    """Map ``TenantCluster.alb_auth_config`` to the ``[ingress.auth]``
    TOML table, or ``None`` when the auth gate is disabled.

    A config is "enabled" only when all three Cognito fields are present
    and non-empty — this matches ``ingress_reconcile._auth_annotation_patch``,
    which treats a partial config as disabled rather than rendering a
    broken auth annotation. Returning ``None`` tells the writer to remove
    the section.
    """
    cfg = alb_auth_config or {}
    if not isinstance(cfg, Mapping):
        return None
    values = {field: str(cfg.get(field) or "") for field in _COGNITO_FIELDS}
    if not all(values.values()):
        return None
    section: dict[str, str] = {"kind": "cognito"}
    section.update(values)
    return section


def ingress_auth_section_for_cluster(cluster: Any) -> dict[str, str] | None:
    """The ``[ingress.auth]`` table describing ``cluster``'s gate.

    Dispatches on the ingress class, because the two classes are gated
    by different configs and the manifest has to record the one actually
    in force. Mirroring only the ALB config meant flipping a cluster to
    nginx -- the migration #1539 exists to enable -- read
    ``alb_auth_config`` as null and stripped ``[ingress.auth]`` from
    every bound app's manifest, leaving the GitOps source of truth
    recording "no auth" for apps that were in fact gated.
    """
    if getattr(cluster, "ingress_class", "alb") == "alb":
        return ingress_auth_section_from_db(getattr(cluster, "alb_auth_config", None))
    return oidc_auth_section_from_db(getattr(cluster, "oidc_auth_config", None))


def oidc_auth_section_from_db(
    oidc_auth_config: Mapping[str, Any] | None,
) -> dict[str, str] | None:
    """Map ``TenantCluster.oidc_auth_config`` to the ``[ingress.auth]``
    TOML table, or ``None`` when the central auth gate is disabled.

    Enabled only when all three routing fields are present and
    non-empty, matching ``core.app_deploy.oidc_auth_for_cluster`` -- so
    the manifest claims a gate exactly when the renderer emits one.
    """
    config = oidc_auth_config or {}
    if not isinstance(config, Mapping):
        return None
    values = {field: str(config.get(field) or "") for field in _OIDC_FIELDS}
    if not all(values.values()):
        return None
    section: dict[str, str] = {"kind": "oidc"}
    section.update(values)
    if config.get("logout_url"):
        section["logout_url"] = str(config["logout_url"])
    return section


def apply_ingress_auth(
    toml_text: str,
    auth_section: dict[str, str] | None,
    *,
    ingress_class: str = "alb",
) -> str:
    """Return ``toml_text`` with its ``[ingress.auth]`` table set to
    ``auth_section`` (or removed when ``auth_section`` is ``None``).

    The rest of the document round-trips through ``tomllib`` ->
    ``tomli_w`` (comments / formatting not preserved). When enabling auth
    on a manifest that has no ``[ingress]`` table yet, an ``[ingress]``
    table is created carrying ``class = ingress_class`` so the rendered
    document is self-consistent. When disabling, only the ``auth`` key is
    removed; an existing ``[ingress]`` table and its other keys are left
    intact, and an ``[ingress]`` table that becomes empty as a result is
    dropped so the manifest doesn't accumulate a bare ``[ingress]``.
    """
    import tomli_w

    data: dict = tomllib_loads(toml_text)
    ingress = data.get("ingress")
    if not isinstance(ingress, dict):
        ingress = {}

    if auth_section is None:
        ingress.pop("auth", None)
        # Drop a now-empty [ingress] table rather than leaving a bare
        # header behind. If it still carries class / other keys, keep it.
        if ingress:
            data["ingress"] = ingress
        else:
            data.pop("ingress", None)
    else:
        if "class" not in ingress:
            ingress["class"] = ingress_class
        ingress["auth"] = dict(auth_section)
        data["ingress"] = ingress

    return tomli_w.dumps(data)


def tomllib_loads(toml_text: str) -> dict:
    """Parse ``toml_text`` to a dict, tolerating empty / blank input.

    A genuinely malformed manifest raises ``tomllib.TOMLDecodeError`` —
    the caller treats that as a skip (we never clobber a manifest we
    can't parse).
    """
    import tomllib

    if not toml_text or not toml_text.strip():
        return {}
    return tomllib.loads(toml_text)


def write_auth_config_to_toml(
    cluster: Any,
    app: RegisteredApp,
    actor_user: Any,
    *,
    fetch: _FetchFn | None = None,
    put: _PutFn | None = None,
    open_pr: _OpenPrFn | None = None,
) -> WriteBackResult:
    """Sync ``cluster``'s edge auth config into ``app``'s ``astrolift.toml``.

    Fetches the current manifest from the app's repo, updates the
    ``[ingress.auth]`` section to match whichever gate the cluster's
    ingress class puts in force (``alb_auth_config`` on ALB,
    ``oidc_auth_config`` elsewhere), and
    commits it back. Returns a :class:`WriteBackResult`; never raises
    (the caller is best-effort). See module docstring for the status
    matrix.

    ``actor_user`` is accepted for symmetry with the audit trail and so
    a future revision can attribute the commit / PR to the operator;
    today the commit attribution follows the ``SourceConnection``'s
    stored credential, so ``actor_user`` only feeds the PR body.
    """
    from astrolift_registry.services.manifest_sync import _pick_source_connection
    from astrolift_scm.providers import ProviderError

    fetch_fn: _FetchFn = fetch if fetch is not None else _default_fetch
    put_fn: _PutFn = put if put is not None else _default_put
    open_pr_fn: _OpenPrFn = open_pr if open_pr is not None else _default_open_pr

    slug = app.slug or str(app.guid)

    if app.source_kind not in _WRITEABLE_SOURCE_KINDS or not app.source_repo:
        log.info(
            "auth write-back skipped for app %s: source_kind=%s source_repo=%r not writeable",
            slug,
            app.source_kind,
            app.source_repo,
        )
        return WriteBackResult(status="skipped", app_slug=slug)

    connection = _pick_source_connection(app)
    if connection is None:
        log.warning(
            "auth write-back skipped for app %s (repo=%s): no active source connection",
            slug,
            app.source_repo,
        )
        return WriteBackResult(status="skipped", app_slug=slug)

    deploy_branch = app.deploy_branch or app.default_branch or "main"
    manifest_path = app.manifest_path or "astrolift.toml"
    ingress_class = getattr(cluster, "ingress_class", "alb") or "alb"
    auth_section = ingress_auth_section_for_cluster(cluster)

    # Fetch current manifest. Absent manifest -> start from empty so we
    # still write the [ingress] table when enabling auth on a repo that
    # hasn't onboarded a manifest yet.
    try:
        current = fetch_fn(connection, app.source_repo, manifest_path, deploy_branch)
    except ProviderError as exc:
        log.warning(
            "auth write-back fetch failed for app %s (repo=%s): %s: %s",
            slug,
            app.source_repo,
            exc.code,
            exc.message,
        )
        return WriteBackResult(status="failed", app_slug=slug, error=f"{exc.code}: {exc.message}")
    except Exception as exc:  # noqa: BLE001 — never propagate to the UI save
        log.exception("auth write-back fetch crashed for app %s (repo=%s)", slug, app.source_repo)
        return WriteBackResult(status="failed", app_slug=slug, error=str(exc) or exc.__class__.__name__)

    current_text = current or ""

    # Compute the patched manifest. A manifest we can't parse is a skip,
    # not a clobber — we never overwrite hand-content we don't understand.
    try:
        updated_text = apply_ingress_auth(current_text, auth_section, ingress_class=ingress_class)
    except Exception as exc:  # noqa: BLE001 — tomllib.TOMLDecodeError + friends
        log.warning(
            "auth write-back skipped for app %s (repo=%s): manifest failed to parse: %s",
            slug,
            app.source_repo,
            exc,
        )
        return WriteBackResult(status="skipped", app_slug=slug, error=str(exc))

    # No-op short-circuit: don't author an empty commit when the repo's
    # [ingress.auth] already matches the desired section. We compare on
    # the auth section specifically (not the whole document) so we leave
    # the repo alone even if other parts would round-trip differently —
    # write-back only owns [ingress.auth], not the operator's formatting.
    if _ingress_auth_in_sync(current_text, auth_section):
        return WriteBackResult(status="unchanged", app_slug=slug)

    commit_message = "chore(astrolift): sync ingress auth config from operator settings"

    # Primary path: commit straight onto the deploy branch (the GitOps
    # source of truth). If the host refuses because the branch is
    # protected, fall back to a feature branch + auto-PR.
    try:
        put_fn(
            connection,
            repo_full_name=app.source_repo,
            path=manifest_path,
            branch=deploy_branch,
            content=updated_text,
            commit_message=commit_message,
        )
    except ProviderError as exc:
        if _is_protected_branch_error(exc):
            return _write_via_pull_request(
                connection=connection,
                app=app,
                slug=slug,
                manifest_path=manifest_path,
                base_branch=deploy_branch,
                content=updated_text,
                commit_message=commit_message,
                actor_user=actor_user,
                put_fn=put_fn,
                open_pr_fn=open_pr_fn,
            )
        log.warning(
            "auth write-back commit failed for app %s (repo=%s): %s: %s",
            slug,
            app.source_repo,
            exc.code,
            exc.message,
        )
        return WriteBackResult(status="failed", app_slug=slug, error=f"{exc.code}: {exc.message}")
    except Exception as exc:  # noqa: BLE001
        log.exception("auth write-back commit crashed for app %s (repo=%s)", slug, app.source_repo)
        return WriteBackResult(status="failed", app_slug=slug, error=str(exc) or exc.__class__.__name__)

    log.info(
        "auth write-back committed for app %s (repo=%s, branch=%s, auth=%s)",
        slug,
        app.source_repo,
        deploy_branch,
        "enabled" if auth_section else "disabled",
    )
    return WriteBackResult(status="committed", app_slug=slug, commit_branch=deploy_branch)


def _write_via_pull_request(
    *,
    connection: SourceConnection,
    app: RegisteredApp,
    slug: str,
    manifest_path: str,
    base_branch: str,
    content: str,
    commit_message: str,
    actor_user: Any,
    put_fn: _PutFn,
    open_pr_fn: _OpenPrFn,
) -> WriteBackResult:
    """Protected-branch fallback: commit to a fresh feature branch and
    open a PR against ``base_branch``."""
    from astrolift_scm.providers import ProviderError

    head_branch = f"astrolift/ingress-auth-{slug}"
    actor_label = getattr(actor_user, "email", "") or getattr(actor_user, "username", "") or "an operator"
    pr_title = "Sync ingress auth config from Astrolift operator settings"
    pr_body = (
        f"Update `{manifest_path}` `[ingress.auth]` for **{app.name or slug}** to "
        f"match the cluster auth gate set in the Astrolift UI by {actor_label}.\n\n"
        f"`{base_branch}` is protected, so this change is proposed as a PR rather "
        "than committed directly."
    )

    try:
        put_fn(
            connection,
            repo_full_name=app.source_repo,
            path=manifest_path,
            branch=head_branch,
            content=content,
            commit_message=commit_message,
        )
        pr = open_pr_fn(
            connection,
            repo_full_name=app.source_repo,
            head_branch=head_branch,
            base_branch=base_branch,
            title=pr_title,
            body=pr_body,
        )
    except ProviderError as exc:
        log.warning(
            "auth write-back PR fallback failed for app %s (repo=%s): %s: %s",
            slug,
            app.source_repo,
            exc.code,
            exc.message,
        )
        return WriteBackResult(status="failed", app_slug=slug, error=f"{exc.code}: {exc.message}")
    except Exception as exc:  # noqa: BLE001
        log.exception("auth write-back PR fallback crashed for app %s (repo=%s)", slug, app.source_repo)
        return WriteBackResult(status="failed", app_slug=slug, error=str(exc) or exc.__class__.__name__)

    pr_url = getattr(pr, "url", "") or ""
    log.info(
        "auth write-back opened PR for app %s (repo=%s, head=%s, base=%s): %s",
        slug,
        app.source_repo,
        head_branch,
        base_branch,
        pr_url,
    )
    return WriteBackResult(
        status="committed",
        app_slug=slug,
        via_pull_request=True,
        pull_request_url=pr_url,
        commit_branch=head_branch,
    )


def write_auth_config_for_cluster(
    cluster: Any,
    actor_user: Any,
    *,
    fetch: _FetchFn | None = None,
    put: _PutFn | None = None,
    open_pr: _OpenPrFn | None = None,
) -> ClusterWriteBackSummary:
    """Write the cluster's ALB auth config back to every app bound to it.

    Apps are discovered the same way ``reconcileClusterIngresses`` finds
    them — through their :class:`AppEnvironment` rows on the cluster — so
    the write-back covers exactly the apps whose live Ingresses the
    reconcile touched. Each distinct app is written at most once even
    when it has several environments on the cluster.

    Per-app failures are recorded in the summary and never abort the
    sweep; the caller logs the aggregate. Never raises.
    """
    from astrolift_lifecycle.models import AppEnvironment

    summary = ClusterWriteBackSummary()

    app_ids = (
        AppEnvironment.objects.filter(
            tenant_cluster=cluster,
            deleted_at__isnull=True,
            registered_app__deleted_at__isnull=True,
        )
        .values_list("registered_app_id", flat=True)
        .distinct()
    )
    apps = RegisteredApp.objects.filter(id__in=list(app_ids)).order_by("id")

    for app in apps:
        try:
            result = write_auth_config_to_toml(
                cluster,
                app,
                actor_user,
                fetch=fetch,
                put=put,
                open_pr=open_pr,
            )
        except Exception as exc:  # noqa: BLE001 — defensive; per-app never aborts the sweep
            log.exception(
                "auth write-back crashed for app %s on cluster %s",
                app.slug,
                getattr(cluster, "slug", cluster.pk),
            )
            result = WriteBackResult(
                status="failed",
                app_slug=app.slug or str(app.guid),
                error=str(exc) or exc.__class__.__name__,
            )
        summary.record(result)

    log.info(
        "auth write-back for cluster %s: repos_updated=%d skipped=%d failed=%d",
        getattr(cluster, "slug", cluster.pk),
        summary.repos_updated,
        summary.skipped,
        summary.failed,
    )
    return summary


def _section_to_db(toml_text: str) -> dict[str, str] | None:
    """Read the ``[ingress.auth]`` Cognito fields out of an existing
    manifest back into the ``alb_auth_config`` shape, so the no-op
    comparison can normalize the repo's current section the same way we
    normalize the DB's. Returns ``None`` when the section is absent /
    partial / unparseable."""
    try:
        data = tomllib_loads(toml_text)
    except Exception:  # noqa: BLE001
        return None
    ingress = data.get("ingress")
    if not isinstance(ingress, dict):
        return None
    auth = ingress.get("auth")
    if not isinstance(auth, dict):
        return None
    return {field: auth.get(field) for field in _COGNITO_FIELDS}


def _ingress_auth_in_sync(toml_text: str, auth_section: dict[str, str] | None) -> bool:
    """True when the repo's ``[ingress.auth]`` already equals the desired
    ``auth_section`` (both absent counts as in-sync)."""
    current = _section_to_db(toml_text)
    current_section = ingress_auth_section_from_db(current)
    return current_section == auth_section


def _is_protected_branch_error(exc: Any) -> bool:
    """Heuristic: does ``exc`` look like a protected-branch rejection?

    GitHub returns 409/422 for a push to a protected branch; the GitHub
    driver surfaces those as ``API_ERROR`` carrying the host body, which
    mentions "protected". GitLab uses similar wording. We match on the
    message rather than the code because the dispatcher flattens the HTTP
    status into a generic ``API_ERROR``.
    """
    message = (getattr(exc, "message", "") or str(exc)).lower()
    needles = ("protected branch", "protected_branch", "branch is protected", "branch protection")
    return any(n in message for n in needles)
