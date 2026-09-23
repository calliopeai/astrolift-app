"""
Apply step for an approved ``SecretChangeProposal`` (#488).

Translates one of the four op kinds (set | delete | attach_bundle |
detach_bundle) into the underlying state change against the manifest
staging buffer or ``AppSecretBundleRef`` table.  Called from the
approve resolver once quorum is reached; idempotent on the proposal
row — if the apply step has already fired the proposal stays in
``applied`` and re-firing is a no-op.

Apply runs synchronously inside the approve mutation (no Temporal
hop) because the underlying ops are pure DB writes; if any fails the
proposal transitions to ``approved`` (not ``applied``), the error is
persisted on ``apply_error``, and the operator can retry via a
follow-up mutation (filed as a future enhancement).
"""

from __future__ import annotations

from dataclasses import dataclass

from django.utils.dateparse import parse_datetime

from astrolift_lifecycle.models import AppEnvironment
from astrolift_manifest.env_edit import delete_app_env_key, read_app_env, set_app_env_keys
from astrolift_manifest.parser import ManifestError, parse_raw
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import (
    AppSecretBundleRef,
    SecretBundle,
    SecretChangeProposal,
)
from astrolift_services.secret_literals import base_raw_digest
from astrolift_services.secret_metadata_ops import (
    retire_app_secret_metadata,
    upsert_app_secret_metadata,
)


@dataclass(slots=True)
class ApplyResult:
    ok: bool
    error: str = ""


def _stage_manifest(app: RegisteredApp, new_text: str, *, actor) -> None:
    """Mirrors the helper in services/schema/mutations.py — kept here
    to avoid the apply path importing the GraphQL module (which would
    invert the dependency).  Validates the new TOML parses then writes
    to the staging buffer (or clears it when the new text equals the
    source-of-truth, matching the no-op semantics)."""
    try:
        parse_raw(new_text)
    except ManifestError as exc:
        raise ManifestError(str(exc)) from exc
    if new_text == (app.manifest_raw or ""):
        app.manifest_raw_staged = ""
    else:
        app.manifest_raw_staged = new_text
    update_fields = ["manifest_raw_staged", "updated_at", "version"]
    if actor is not None:
        app.updated_by = actor
        update_fields.append("updated_by")
    app.save(update_fields=update_fields)


def _stamp_base(proposal: SecretChangeProposal, app: RegisteredApp, key: str) -> None:
    """Record which ``manifest_raw`` value for ``key`` this approval was
    applied against. The deploy path honours the approval only while the
    key still has that value there (#1758)."""
    base = read_app_env(app.manifest_raw or "").get(key)
    proposal.payload = {**(proposal.payload or {}), "base_raw_digest": base_raw_digest(base)}
    proposal.save(update_fields=["payload", "updated_at", "version"])


def apply_proposal(
    proposal: SecretChangeProposal,
    *,
    actor=None,
) -> ApplyResult:
    """Apply the approved proposal's op to the underlying state.

    Returns an ``ApplyResult`` carrying success + any error message.
    The caller is responsible for transitioning the proposal status —
    this function does not flip ``proposal.status`` itself so callers
    can wrap the apply + status transition in a single transaction.
    """
    op = proposal.op
    payload = proposal.payload or {}
    app = proposal.registered_app

    try:
        if op == SecretChangeProposal.Op.SET.value:
            key = (payload.get("key") or "").strip()
            value = payload.get("value") or ""
            if not key:
                return ApplyResult(ok=False, error="payload.key is required for set")
            _stamp_base(proposal, app, key)
            source = app.manifest_raw_staged or app.manifest_raw or ""
            new_text = set_app_env_keys(source, {key: value})
            _stage_manifest(app, new_text, actor=actor)
            # The metadata a direct setAppSecret records, from the payload
            # the proposer submitted. Without it an approved key restricted
            # to production fell back to scope "all" and reached previews
            # (#1758). A missing scope keeps the stored one.
            expires_at = payload.get("expires_at")
            upsert_app_secret_metadata(
                app=app,
                key=key,
                environment_name="",
                expires_at=parse_datetime(expires_at) if expires_at else None,
                set_via=payload.get("set_via"),
                scope=payload.get("scope"),
                actor=actor,
            )
            return ApplyResult(ok=True)

        if op == SecretChangeProposal.Op.DELETE.value:
            key = (payload.get("key") or "").strip()
            if not key:
                return ApplyResult(ok=False, error="payload.key is required for delete")
            _stamp_base(proposal, app, key)
            source = app.manifest_raw_staged or app.manifest_raw or ""
            new_text, removed = delete_app_env_key(source, key)
            if not removed:
                # Idempotent: deleting an already-absent key is a no-op
                # at apply time.  The proposal was valid when staged; if
                # someone else removed the key between propose and
                # approve, we shouldn't fail the apply.
                return ApplyResult(ok=True)
            _stage_manifest(app, new_text, actor=actor)
            # Retire the metadata as a direct deleteAppSecret does, but only
            # once manifest_raw no longer carries the key either. While it
            # does, the delete is a draft, and discarding the draft brings
            # the key back; with its metadata gone it would come back
            # scoped "all" and reach previews.
            if key not in read_app_env(app.manifest_raw or ""):
                retire_app_secret_metadata(app, key, actor=actor)
            return ApplyResult(ok=True)

        if op == SecretChangeProposal.Op.ATTACH_BUNDLE.value:
            bundle_slug = (payload.get("bundle_slug") or "").strip()
            prefix = payload.get("prefix") or ""
            env_name = proposal.environment_name or ""
            if not bundle_slug or not env_name:
                return ApplyResult(
                    ok=False,
                    error="payload.bundle_slug and environment are required for attach_bundle",
                )
            env = AppEnvironment.objects.filter(
                registered_app=app,
                name=env_name,
                deleted_at__isnull=True,
            ).first()
            if env is None:
                return ApplyResult(ok=False, error=f"environment {env_name!r} not found")
            # Org-scope the apply-time re-resolution (mirrors the
            # propose/attach paths). SecretBundle.slug is unique only
            # per (team, slug) — NOT per org — so an unscoped
            # ``.filter(slug=...).first()`` can return another org's
            # same-slug bundle (pk order), which then either trips the
            # org-mismatch guard and fails a legitimate proposal, or —
            # for a same-org/other-team collision — silently attaches
            # the wrong bundle. Scoping to the app's org closes the
            # cross-org hole and matches how the proposal was validated
            # at propose time.
            bundle = SecretBundle.objects.filter(
                slug=bundle_slug,
                organization_id=app.organization_id,
                deleted_at__isnull=True,
            ).first()
            if bundle is None:
                return ApplyResult(ok=False, error=f"bundle {bundle_slug!r} not found")
            if bundle.organization_id != app.organization_id:
                return ApplyResult(
                    ok=False,
                    error="bundle and app belong to different organizations",
                )
            existing = AppSecretBundleRef.objects.filter(
                registered_app=app,
                app_environment=env,
                secret_bundle=bundle,
                deleted_at__isnull=True,
            ).first()
            if existing is not None:
                # Idempotent: same (app, env, bundle) attachment with the
                # same prefix is a no-op; differing prefix updates.
                if existing.prefix != prefix:
                    existing.prefix = prefix
                    update_fields = ["prefix", "updated_at", "version"]
                    if actor is not None:
                        existing.updated_by = actor
                        update_fields.append("updated_by")
                    existing.save(update_fields=update_fields)
                return ApplyResult(ok=True)
            AppSecretBundleRef.objects.create(
                registered_app=app,
                app_environment=env,
                secret_bundle=bundle,
                prefix=prefix,
                created_by=actor,
                updated_by=actor,
            )
            return ApplyResult(ok=True)

        if op == SecretChangeProposal.Op.DETACH_BUNDLE.value:
            attachment_id = (payload.get("attachment_id") or "").strip()
            if not attachment_id:
                return ApplyResult(
                    ok=False,
                    error="payload.attachment_id is required for detach_bundle",
                )
            # Org-scope the apply-time re-resolution so a proposal can
            # only ever detach an attachment inside its own org, even if
            # a guid from another tenant were somehow presented. A
            # foreign / unknown guid resolves to None → treated as the
            # idempotent "already gone" success below (never touches the
            # other org's row).
            ref = AppSecretBundleRef.objects.filter(
                guid=attachment_id,
                registered_app__organization_id=app.organization_id,
            ).first()
            if ref is None or ref.deleted_at is not None:
                # Idempotent: attachment was already gone — treat as
                # success so re-runs don't surface spurious failures.
                return ApplyResult(ok=True)
            ref.soft_delete(by=actor)
            return ApplyResult(ok=True)

        return ApplyResult(ok=False, error=f"unknown op {op!r}")
    except ManifestError as exc:
        return ApplyResult(ok=False, error=f"manifest parse failed: {exc}")
    except Exception as exc:  # noqa: BLE001 — apply errors land on the row
        return ApplyResult(ok=False, error=str(exc) or "apply failed")
