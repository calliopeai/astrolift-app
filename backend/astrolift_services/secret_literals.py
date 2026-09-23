"""Literal app-secret resolution shared by the secrets UI and the deploy path (#1758).

App-wide ``[env]`` literals (written by the setAppSecret / rotateAppSecret /
deleteAppSecret / bulkImportAppSecrets mutations) live in the manifest text
buffer, not a per-secret DB row -- see ``AppSecretMetadata``'s docstring.
Two independent consumers ask "which keys are visible in this environment":

* ``astrolift_services.schema.queries._list_app_secrets`` -- the UI's
  secret list.
* ``core.app_deploy.render_resources_for_deployment`` /
  ``astrolift_workflows.activities.app_lifecycle._update_secrets_sync`` --
  what actually reaches a workload's envFrom.

Scope filtering (``AppSecretMetadata.scope`` vs. the queried environment)
is a visibility boundary -- a preview-scoped secret leaking into
production (or vice versa) is a real leak, not a display glitch -- so both
consumers share this one predicate instead of maintaining their own copy.
The deploy path is stricter than the list in two ways: under secret
approval it deploys only approved staged changes, and it recognises a
preview by identity rather than by build status (#1758 review, H1/H2).
"""

from __future__ import annotations

import logging

from astrolift_lifecycle.models.preview_environment import PreviewEnvironment
from astrolift_manifest.env_edit import read_app_env
from astrolift_services.models import AppSecretMetadata, SecretChangeProposal

log = logging.getLogger(__name__)


def allowed_scopes_for_env(env_name: str, preview_env_branches: dict[str, str]) -> frozenset[str]:
    """Compute the set of scope values that a secret must have to be
    visible in ``env_name``.

    Preview envs accept ``all``, ``preview``, and ``preview:<branch>``.
    All other envs (production, staging, …) accept ``all`` and
    ``production``.
    """
    if env_name in preview_env_branches:
        branch = preview_env_branches[env_name]
        return frozenset({"all", "preview", f"preview:{branch}"})
    return frozenset({"all", "production"})


def _latest_applied_literal_changes(app) -> dict[str, str | None]:
    """Per key, what the most recent applied SET/DELETE proposal made it:
    the value it set, or None when it deleted the key."""
    latest: dict[str, str | None] = {}
    applied = (
        SecretChangeProposal.objects.filter(
            registered_app=app,
            status=SecretChangeProposal.Status.APPLIED.value,
            op__in=(SecretChangeProposal.Op.SET.value, SecretChangeProposal.Op.DELETE.value),
        )
        .order_by("-applied_at", "-pk")
        .values_list("op", "payload")
    )
    for op, payload in applied:
        payload = payload or {}
        key = str(payload.get("key") or "").strip()
        if not key or key in latest:
            continue
        latest[key] = None if op == SecretChangeProposal.Op.DELETE.value else str(payload.get("value") or "")
    return latest


def _deployable_literals(app) -> dict[str, str]:
    """The app-wide ``[env]`` literals a deploy may put in front of a workload.

    Without secret approval that is ``manifest_raw_staged`` when set, else
    ``manifest_raw``. The set/rotate/delete/bulk-import mutations edit only
    the staged buffer, so a literal takes effect on the next deploy
    without a push-to-repo round trip (#1758).

    With approval on (#488) the staged buffer cannot be trusted on its
    own: ``updateManifest`` and ``bulkImportAppSecrets`` write it without
    creating a proposal (#1758 review, H1). So the deploy starts from
    ``manifest_raw``, and a staged change to a key is taken only when the
    latest applied proposal for that key produced exactly that change.
    Anything else keeps the ``manifest_raw`` value. Anchoring on the
    staged buffer, rather than replaying every applied proposal over
    ``manifest_raw``, keeps an old approval from overriding a later repo
    change to the same key: once a draft is pushed and synced the buffer
    is cleared and the key is no longer a pending change.
    """
    raw = read_app_env(app.manifest_raw or "")
    if not app.manifest_raw_staged:
        return raw
    staged = read_app_env(app.manifest_raw_staged)
    if not app.requires_secret_approval:
        return staged

    pending = sorted(key for key in raw.keys() | staged.keys() if raw.get(key) != staged.get(key))
    if not pending:
        return raw
    approved = _latest_applied_literal_changes(app)
    out = dict(raw)
    unapproved: list[str] = []
    for key in pending:
        if key not in approved or approved[key] != staged.get(key):
            unapproved.append(key)
        elif key in staged:
            out[key] = staged[key]
        else:
            out.pop(key, None)
    if unapproved:
        # Key names only; the UI lists them anyway. Without this an
        # operator sees the staged value in the secrets list and cannot
        # tell why the pod never got it.
        log.warning(
            "app %s requires secret approval; staged [env] changes to %s have no matching "
            "applied proposal and are not deployed",
            app.slug,
            ", ".join(unapproved),
        )
    return out


def literal_secrets_for_environment(app, environment) -> dict[str, str]:
    """App-wide ``[env]`` literal secrets visible to one ``AppEnvironment``:
    ``_deployable_literals`` filtered by scope.

    Filters out keys whose ``AppSecretMetadata.scope`` doesn't match the
    environment, with the same ``allowed_scopes_for_env`` rule the UI's
    secret list uses (#752).
    """
    literals = _deployable_literals(app)
    if not literals:
        return {}

    meta_index: dict[tuple[str, str], AppSecretMetadata] = {
        (m.environment_name, m.key): m
        for m in AppSecretMetadata.objects.filter(
            registered_app=app,
            key__in=list(literals.keys()),
            deleted_at__isnull=True,
        )
    }
    # A preview by identity, not by current status: a FAILED or torn-down
    # preview (its row soft-deleted) is still a preview, and a status or
    # deleted_at filter here made it fall through to the production
    # branch and receive production-scoped secrets (#1758 review, H2).
    # The lineage FK also counts, for a preview whose row is gone.
    preview_branch = (
        PreviewEnvironment.all_objects.filter(app_environment=environment)
        .order_by("-created_at", "-pk")
        .values_list("branch", flat=True)
        .first()
    )
    is_preview = preview_branch is not None or environment.previewed_environment_id is not None
    preview_env_branches = {environment.name: preview_branch or ""} if is_preview else {}
    allowed = allowed_scopes_for_env(environment.name, preview_env_branches)

    out: dict[str, str] = {}
    for key, value in literals.items():
        meta = meta_index.get((environment.name, key)) or meta_index.get(("", key))
        scope = meta.scope if meta else "all"
        if scope in allowed:
            out[key] = value
    return out
