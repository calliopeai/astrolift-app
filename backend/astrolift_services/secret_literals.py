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


def literal_secrets_for_environment(app, environment) -> dict[str, str]:
    """App-wide ``[env]`` literal secrets visible to one ``AppEnvironment``.

    Reads ``manifest_raw_staged`` when set and falls back to
    ``manifest_raw`` otherwise -- a set/rotate/delete/bulk-import mutation
    edits only the staged buffer, never ``manifest_raw`` directly (see
    ``astrolift_services.schema.mutations.secrets``), so a literal takes
    effect on the next deploy without a push-to-repo round trip (#1758).

    Filters out keys whose ``AppSecretMetadata.scope`` doesn't match the
    environment, with the same ``allowed_scopes_for_env`` rule the UI's
    secret list uses (#752).
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
