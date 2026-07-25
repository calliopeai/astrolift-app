"""Alert-rule cleanup — the deprovision-side mirror of :mod:`alert_seed`.

``alert_seed.seed_default_alert_rules`` plants one :class:`AlertRule` per
default template when an app is registered, each tagged ``target="app"`` /
``target_id=<app.slug>`` within the app's organization. There is **no**
foreign key from an ``AlertRule`` back to a ``RegisteredApp`` — the
ownership link is that ``(target, target_id, organization)`` triple, a soft
slug reference.

That soft link is exactly why the rows leaked. Every per-resource deregister
step keys its cleanup off a ``registered_app`` foreign key, so the alert
rules were invisible to the teardown's FK-driven soft-delete pass and
survived it: the app's rows (and the app itself) were soft-deleted while its
alert rules stayed live, so the /alerts page kept listing alerts for a
deleted app (reported for ``smd-fileportal``).

This module is the ORM bridge that closes the link on the way out — the
inverse of the seeder that opened it:

* :func:`soft_delete_app_alert_rules` — soft-delete every live app-targeted
  rule owned by one ``(org, slug)``. Wired into the deregister teardown's
  final soft-delete pass so an app's alert rules die with it.
* :func:`find_orphaned_app_alert_rules` — enumerate app-targeted rules whose
  owning app is already gone (hard-absent or soft-deleted). Backs the
  ``cleanup_orphaned_alert_rules`` management command that clears the rows a
  pre-fix deregister already stranded.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from astrolift_operations.models import AlertRule

log = logging.getLogger(__name__)


def soft_delete_app_alert_rules(*, organization_id: int, app_slug: str, by=None) -> int:
    """Soft-delete every live app-targeted ``AlertRule`` owned by ``(org, slug)``.

    Ownership mirror of :func:`alert_seed.seed_default_alert_rules`: a rule
    belongs to an app when ``target == 'app'`` and ``target_id == app.slug``
    within the app's organization. Returns the count soft-deleted.

    Idempotent: the default manager already excludes soft-deleted rows, so a
    re-run over an already-cleaned app soft-deletes nothing. Best-effort per
    row — one malformed row logs a warning and is skipped rather than
    stranding the rest of an app teardown (matches the seeder's per-row
    isolation)."""
    qs = AlertRule.objects.filter(
        organization_id=organization_id,
        target=AlertRule.Target.APP.value,
        target_id=app_slug,
    )
    count = 0
    for rule in qs:
        try:
            rule.soft_delete(by=by)
            count += 1
        except Exception:  # noqa: BLE001 — one bad row must not skip the rest
            log.warning(
                "alert cleanup: failed to soft-delete rule id=%s name=%r for app %s",
                rule.pk,
                rule.name,
                app_slug,
                exc_info=True,
            )
    return count


def find_orphaned_app_alert_rules() -> Iterator[tuple[int, str, list[AlertRule]]]:
    """Yield ``(organization_id, app_slug, [rules])`` per group of live
    app-targeted alert rules whose owning ``RegisteredApp`` is gone.

    "Gone" means no live (non-soft-deleted) app exists with that
    ``(organization, slug)``. Because the default manager excludes
    soft-deleted apps, a soft-deleted owner reads as absent — which is the
    ``smd-fileportal`` case (app soft-deleted on deregister, rules left
    behind) as well as a hard-absent app.

    Slug reuse is handled correctly: if a NEW app has since re-claimed the
    slug it is live, so the group is NOT orphaned — those rules are the ones
    currently in effect for the reclaimed slug (the idempotent seeder reuses
    them by name)."""
    from astrolift_registry.models import RegisteredApp

    groups: dict[tuple[int, str], list[AlertRule]] = {}
    for rule in AlertRule.objects.filter(target=AlertRule.Target.APP.value).order_by(
        "organization_id", "target_id", "pk"
    ):
        groups.setdefault((rule.organization_id, rule.target_id), []).append(rule)

    for (org_id, slug), rules in groups.items():
        # An empty target_id can't be tied to an app — skip it. Defensive:
        # the seeder always sets target_id, but we must never treat "" as a
        # wildcard that matches (and orphan-deletes) unrelated rows.
        if not slug:
            continue
        owner_live = RegisteredApp.objects.filter(
            organization_id=org_id,
            slug=slug,
        ).exists()
        if not owner_live:
            yield org_id, slug, rules


__all__ = [
    "find_orphaned_app_alert_rules",
    "soft_delete_app_alert_rules",
]
