"""Writes to the ``AppSecretMetadata`` sidecar (#677 / #678 / #752).

Shared by the direct secret mutations and the proposal apply path
(``astrolift_services.secret_change_apply``), which must not import the
GraphQL mutation package. An approved proposal has to leave the same
metadata a direct write would, or its key's scope silently falls back to
``all`` (#1758).
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from django.utils import timezone

from astrolift_services.models import AppSecretMetadata

if TYPE_CHECKING:
    from astrolift_registry.models import RegisteredApp

VALID_SECRET_SOURCES = {s.value for s in AppSecretMetadata.Source}


def upsert_app_secret_metadata(
    *,
    app: RegisteredApp,
    key: str,
    environment_name: str = "",
    expires_at: dt.datetime | None = None,
    set_via: str | None = None,
    scope: str | None = None,
    actor=None,
) -> AppSecretMetadata:
    """Upsert the operator-facing metadata sidecar for a secret literal.

    Idempotent on (registered_app, environment_name, key).  Each call
    refreshes ``set_at`` to ``timezone.now()`` so the FE can render a
    'set on <date>' tooltip independent of the underlying audit row.

    ``expires_at=None`` + ``set_via=None`` is a no-op on the timestamp /
    expiry but still touches ``set_at`` — operators sometimes want a
    'last-touched' refresh without changing the data, and the cost of
    one UPDATE per literal write is negligible against the platform's
    overall throughput.

    ``scope=None`` is "don't touch" as well: writing a default on every
    update let a rotate or a bulk import silently widen a key restricted
    to ``production`` back to ``all`` (#1758). On create, an app-wide row
    starts at ``all``, and a per-environment row stores no scope, so it
    follows the app-wide row (``scope_in_force``). The deploy prefers the
    per-environment row: created as ``all`` to record an expiry, it
    widened a production-only key into that environment (#1946), and
    created as a copy of the app-wide scope, it kept an approved app-wide
    narrowing from ever reaching that environment.
    """
    if set_via is not None and set_via not in VALID_SECRET_SOURCES:
        # Reject unknown sources up front so a typo doesn't silently
        # land an out-of-band value on the column.
        set_via = AppSecretMetadata.Source.WEB.value
    environment_name = environment_name or ""
    row = AppSecretMetadata.objects.filter(
        registered_app=app,
        environment_name=environment_name,
        key=key,
        deleted_at__isnull=True,
    ).first()
    if row is None:
        row = AppSecretMetadata.objects.create(
            registered_app=app,
            environment_name=environment_name,
            key=key,
            expires_at=expires_at,
            source=(set_via or AppSecretMetadata.Source.WEB.value),
            scope=scope if scope is not None else (None if environment_name else "all"),
            set_at=timezone.now(),
            created_by=actor,
            updated_by=actor,
        )
        return row
    # Apply optional updates atomically.  We don't clear
    # ``expires_at`` to None or reset ``scope`` unless the caller
    # explicitly passes a value — `None` means "don't touch" per the
    # input contract.
    updates: dict = {"set_at": timezone.now()}
    if expires_at is not None:
        updates["expires_at"] = expires_at
    if set_via is not None:
        updates["source"] = set_via
    if scope is not None:
        updates["scope"] = scope
    for k, v in updates.items():
        setattr(row, k, v)
    if actor is not None:
        row.updated_by = actor
    row.save(
        update_fields=[*updates.keys(), "updated_by", "updated_at"],
    )
    return row


def scope_in_force(environment_scope: str | None, app_wide_scope: str | None) -> str:
    """The scope that governs a key in one environment: the scope of its
    per-environment row, else its app-wide row's, else ``all``. None is a
    missing row or a per-environment row that follows the app-wide one.
    The deploy path, the secrets list and the approval check all resolve
    a key's scope here."""
    if environment_scope is not None:
        return environment_scope
    if app_wide_scope is not None:
        return app_wide_scope
    return "all"


def current_secret_scope(app, key: str, environment_name: str = "") -> str:
    """``scope_in_force`` for ``key`` in ``environment_name``, from the
    stored rows."""
    scopes = dict(
        AppSecretMetadata.objects.filter(
            registered_app=app,
            key=key,
            environment_name__in={environment_name, ""},
            deleted_at__isnull=True,
        ).values_list("environment_name", "scope")
    )
    return scope_in_force(scopes.get(environment_name), scopes.get(""))


def retire_app_secret_metadata(app, key: str, *, actor=None) -> None:
    """Soft-delete every metadata row for ``key``, so a later re-add
    doesn't inherit a stale expiry, source or scope."""
    for row in AppSecretMetadata.objects.filter(registered_app=app, key=key, deleted_at__isnull=True):
        row.soft_delete(by=actor)
