"""Activities for ``DeregisterAppWorkflow`` (#392) — danger-zone hard
deregister of a RegisteredApp.

The workflow re-uses the per-step activities the platform already
ships (``delete_app_namespaces``, ``revoke_app_deploy_tokens``,
``soft_delete_app_records``, ``deprovision_app_registry_repo``,
``deprovision_app_identity_role``, ``deprovision_managed_service``).
The two operations the existing surface didn't cover are wired here:

* ``delete_app_source_webhook`` — DELETE the push-event webhook the
  source-webhook install path (``astrolift_scm.services.webhooks``)
  registered on the app's source repo. Idempotent: a missing hook,
  GitHub-App connections (which deliver via the App's own webhook,
  not a per-repo hook), and apps with no source repo all short-
  circuit cleanly. Clears the platform's ``source_webhook_*``
  bookkeeping so a future install starts fresh.

* ``delete_app_materialized_secrets`` — fan out
  ``delete_secret_from_cluster`` for every (cluster, app, env) where
  the app had an active ``AppSecretBundleRef`` materialized into a
  k8s Secret, then soft-delete the ``AppSecretBundleRef`` rows so
  the operator's per-bundle bindings are revoked alongside the
  cluster-side cleanup.

Both activities follow the same shape as the other deregister
primitives: a thin ``@activity.defn`` wrapper around a ``_sync``
helper, returning a JSON-safe summary dict the workflow rolls into
``WorkflowResult.data["teardown"][<resource>]``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from temporalio import activity

log = logging.getLogger("astrolift_workflows.activities.app_deregister")


# ---- Source webhook -----------------------------------------------


def _delete_source_webhook_sync(registered_app_id: int) -> dict[str, Any]:
    """Delete the source-host push-event webhook for the app.

    Returns a summary dict carrying ``status`` (one of ``deleted``,
    ``not_installed``, ``app_install``, ``no_repo``, ``no_connection``)
    + an operator-facing detail string. The workflow surfaces this so
    a partial-failure resume can tell the operator which case the
    webhook landed in.
    """
    from astrolift_registry.models import RegisteredApp
    from astrolift_scm.models import SourceConnection
    from astrolift_scm.providers import ProviderError, delete_webhook
    from astrolift_scm.services.webhooks import _pick_source_connection

    app = RegisteredApp.all_objects.select_related(
        "organization",
    ).get(pk=registered_app_id)

    repo = (app.source_repo or "").strip()
    if not repo:
        return {
            "status": "no_repo",
            "detail": "app has no source repo configured",
            "hook_id": "",
        }

    hook_id = (app.source_webhook_id or "").strip()
    connection: SourceConnection | None = _pick_source_connection(app)

    def _clear_bookkeeping() -> None:
        app.source_webhook_id = ""
        app.source_webhook_installed_at = None
        app.save(
            update_fields=[
                "source_webhook_id",
                "source_webhook_installed_at",
                "updated_at",
                "version",
            ],
        )

    if connection is None and not hook_id:
        return {
            "status": "not_installed",
            "detail": "no webhook was ever installed for this app",
            "hook_id": "",
        }

    if connection is None:
        # No active connection but the row carries a hook_id —
        # nothing to call against. Clear the bookkeeping so a future
        # install starts clean.
        _clear_bookkeeping()
        return {
            "status": "no_connection",
            "detail": (
                "no active source connection available to delete the hook; " "cleared platform bookkeeping"
            ),
            "hook_id": hook_id,
        }

    if connection.kind == "github_app_install":
        # GitHub-App connections never install a per-repo hook — the
        # App's own webhook fires. Nothing to delete; just clear our
        # marker timestamps so the UI reflects the deregister.
        _clear_bookkeeping()
        return {
            "status": "app_install",
            "detail": (
                "github_app_install delivers webhooks via the App; "
                "no per-repo hook to delete. Cleared platform bookkeeping."
            ),
            "hook_id": "",
        }

    if not hook_id:
        # We have a connection but no recorded hook id — likely a
        # connection from before the webhook ever installed, or a
        # refresh path that never minted one. Treat as not-installed.
        _clear_bookkeeping()
        return {
            "status": "not_installed",
            "detail": "no recorded webhook id; nothing to delete on the host",
            "hook_id": "",
        }

    try:
        delete_webhook(
            connection,
            repo_full_name=repo,
            hook_id=hook_id,
        )
    except ProviderError as exc:
        # Surface up to the workflow as a failure so partial-resume
        # can list it as a still-live resource and the operator can
        # retry without losing the rest of the teardown.
        log.warning(
            "delete_source_webhook host error app=%s repo=%s hook=%s: %s",
            app.slug,
            repo,
            hook_id,
            exc.message,
        )
        raise

    _clear_bookkeeping()
    return {
        "status": "deleted",
        "detail": f"deleted webhook {hook_id!r} on {repo}",
        "hook_id": hook_id,
    }


@activity.defn(name="astrolift.app.delete_source_webhook")
async def delete_app_source_webhook(registered_app_id: int) -> dict[str, Any]:
    """Delete the push-event webhook from the app's source repo.

    Idempotent: not-installed / no-repo / no-connection / app-install
    cases all return a status the workflow logs and treats as success.
    Host errors propagate so the workflow can record the resource as
    still-live and let the operator retry.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    summary = await sync_to_async(_delete_source_webhook_sync)(
        registered_app_id,
    )
    log.info(
        "delete_app_source_webhook app_id=%s status=%s hook_id=%s",
        registered_app_id,
        summary["status"],
        summary["hook_id"],
    )
    return summary


# ---- Materialized cluster secrets ---------------------------------


def _list_app_secret_targets_sync(registered_app_id: int) -> list[dict[str, Any]]:
    """Return one row per (cluster, env, bundle) where the app has an
    active ``AppSecretBundleRef`` — the per-cluster materialized
    Secret cleanup uses this list to enumerate what to drop.

    Returning serialized dicts (not ORM rows) keeps the result
    transmissible through Temporal's JSON converter, matching the
    shape ``list_active_secret_bundle_targets`` returns for the
    rotation workflow.
    """
    from astrolift_services.models import AppSecretBundleRef

    refs = list(
        AppSecretBundleRef.objects.filter(
            registered_app_id=registered_app_id,
            deleted_at__isnull=True,
        ).select_related(
            "registered_app__organization",
            "app_environment__tenant_cluster",
            "secret_bundle",
        ),
    )
    out: list[dict[str, Any]] = []
    for ref in refs:
        cluster = ref.app_environment.tenant_cluster if ref.app_environment else None
        if cluster is None:
            # No cluster bound — the rotation/delete path skips this
            # case too. Soft-delete still needs to happen, so we
            # surface a None-cluster sentinel row.
            out.append(
                {
                    "ref_id": ref.pk,
                    "registered_app_id": ref.registered_app_id,
                    "app_environment_id": ref.app_environment_id,
                    "tenant_cluster_id": None,
                    "app_slug": ref.registered_app.slug,
                    "bundle_slug": ref.secret_bundle.slug,
                    "bundle_backend_ref": ref.secret_bundle.backend_ref,
                    "prefix": ref.prefix or "",
                },
            )
            continue
        out.append(
            {
                "ref_id": ref.pk,
                "registered_app_id": ref.registered_app_id,
                "app_environment_id": ref.app_environment_id,
                "tenant_cluster_id": cluster.pk,
                "app_slug": ref.registered_app.slug,
                "bundle_slug": ref.secret_bundle.slug,
                "bundle_backend_ref": ref.secret_bundle.backend_ref,
                "prefix": ref.prefix or "",
            },
        )
    return out


@activity.defn(name="astrolift.app.list_secret_targets")
async def list_app_secret_targets(
    registered_app_id: int,
) -> list[dict[str, Any]]:
    """Fan-out plan for the deregister's materialized-secret cleanup.

    Returns one row per active ``AppSecretBundleRef`` on the app, in
    the same shape ``list_active_secret_bundle_targets`` emits for
    the rotation workflow so ``delete_secret_from_cluster`` accepts
    each row verbatim.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    rows = await sync_to_async(_list_app_secret_targets_sync)(
        registered_app_id,
    )
    log.info(
        "list_app_secret_targets app_id=%s returned %d row(s)",
        registered_app_id,
        len(rows),
    )
    return rows


def _revoke_app_secret_bundle_refs_sync(registered_app_id: int) -> int:
    """Soft-delete every active ``AppSecretBundleRef`` on the app.

    Mirrors the SOFT_DELETE record-kind catalog in the policy module:
    the bundle (the secret values themselves) survive; the binding
    that points the app at the bundle is what gets revoked. Future
    re-registers of the same slug land a fresh ref row through the
    normal onboarding path.
    """
    from astrolift_services.models import AppSecretBundleRef

    now = datetime.now(UTC)
    qs = AppSecretBundleRef.objects.filter(
        registered_app_id=registered_app_id,
        deleted_at__isnull=True,
    )
    count = 0
    for ref in qs:
        ref.deleted_at = now
        ref.save(update_fields=["deleted_at", "updated_at", "version"])
        count += 1
    return count


@activity.defn(name="astrolift.app.revoke_secret_bundle_refs")
async def revoke_app_secret_bundle_refs(registered_app_id: int) -> int:
    """Soft-delete every active ``AppSecretBundleRef`` for the app.

    Fired AFTER ``delete_secret_from_cluster`` has run for each
    target — once the materialized k8s Secret is gone we can drop the
    binding row that pointed at it.
    """
    from asgiref.sync import sync_to_async

    activity.heartbeat()
    n = await sync_to_async(_revoke_app_secret_bundle_refs_sync)(
        registered_app_id,
    )
    log.info(
        "revoke_app_secret_bundle_refs app_id=%s revoked=%d",
        registered_app_id,
        n,
    )
    return n


__all__ = [
    "_delete_source_webhook_sync",
    "_list_app_secret_targets_sync",
    "_revoke_app_secret_bundle_refs_sync",
    "delete_app_source_webhook",
    "list_app_secret_targets",
    "revoke_app_secret_bundle_refs",
]
