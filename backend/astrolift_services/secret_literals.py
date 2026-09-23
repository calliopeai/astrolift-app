"""Literal app-secret resolution shared by the secrets UI and the deploy path (#1758).

App-wide ``[env]`` literals (written by the setAppSecret / rotateAppSecret /
deleteAppSecret / bulkImportAppSecrets mutations) live in the manifest text
buffer, not a per-secret DB row -- see ``AppSecretMetadata``'s docstring.
Two independent consumers need the exact same "which keys are visible in
this environment" answer:

* ``astrolift_services.schema.queries._list_app_secrets`` -- the UI's
  secret list.
* ``core.app_deploy.render_resources_for_deployment`` /
  ``astrolift_workflows.activities.app_lifecycle._update_secrets_sync`` --
  what actually reaches a workload's envFrom.

Scope filtering (``AppSecretMetadata.scope`` vs. the queried environment)
is a visibility boundary -- a preview-scoped secret leaking into
production (or vice versa) is a real leak, not a display glitch -- so both
consumers share this one predicate instead of maintaining their own copy.
"""

from __future__ import annotations

from astrolift_lifecycle.models.preview_environment import PreviewEnvironment
from astrolift_manifest.env_edit import read_app_env
from astrolift_services.models import AppSecretMetadata


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


def literal_secrets_for_environment(app, environment_name: str) -> dict[str, str]:
    """App-wide ``[env]`` literal secrets visible to one environment.

    Reads ``manifest_raw_staged`` when set and falls back to
    ``manifest_raw`` otherwise -- a set/rotate/delete/bulk-import mutation
    edits only the staged buffer, never ``manifest_raw`` directly (see
    ``astrolift_services.schema.mutations.secrets``), so a literal takes
    effect on the next deploy without a push-to-repo round trip (#1758).

    Filters out keys whose ``AppSecretMetadata.scope`` doesn't match
    ``environment_name``, mirroring ``_list_app_secrets``'s per-env
    visibility rule (#752) so the UI list and what reaches a workload's
    envFrom can never disagree about which keys apply where.
    """
    literals = read_app_env(app.manifest_raw_staged or app.manifest_raw or "")
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
    preview_branch = (
        PreviewEnvironment.objects.filter(
            registered_app=app,
            app_environment__name=environment_name,
            deleted_at__isnull=True,
            status__in=(
                PreviewEnvironment.Status.BUILDING,
                PreviewEnvironment.Status.RUNNING,
            ),
        )
        .values_list("branch", flat=True)
        .first()
    )
    preview_env_branches = {environment_name: preview_branch} if preview_branch else {}
    allowed = allowed_scopes_for_env(environment_name, preview_env_branches)

    out: dict[str, str] = {}
    for key, value in literals.items():
        meta = meta_index.get((environment_name, key)) or meta_index.get(("", key))
        scope = meta.scope if meta else "all"
        if scope in allowed:
            out[key] = value
    return out
