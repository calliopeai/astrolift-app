"""
Pre-rendered diff for a ``SecretChangeProposal`` (#488).

Built at propose time so the proposal-detail page can render side-by-
side current → proposed without re-reading + re-diffing.  Values are
intentionally masked at diff time — the operator can reveal them via
the explicit ``revealAppSecret`` mutation (#424) which carries its
own audit trail; baking plaintext into the diff blob would scatter
secrets into every query that returns the proposal.

Shape of the returned dict::

    {
        "op": "set",                # mirror of proposal.op for the FE
        "before": {                 # current state (masked)
            "key": "API_KEY",
            "value_masked": "abc***",
            "present": True,
        },
        "after": {                  # proposed state (masked)
            "key": "API_KEY",
            "value_masked": "xyz***",
            "present": True,
        },
        "summary": "Update API_KEY in (app-wide)",
    }
"""

from __future__ import annotations

from typing import Any

from astrolift_manifest.env_edit import read_app_env
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import AppSecretBundleRef, SecretChangeProposal
from astrolift_services.secret_metadata_ops import current_secret_scope, environment_scopes


def _mask(value: str) -> str:
    """Mask the value, leaving the first 3 + last 1 chars for hint.
    Shorter values just become ``***``."""
    if not value:
        return ""
    if len(value) <= 4:
        return "***"
    return f"{value[:3]}***{value[-1]}"


def _kept_scopes(app: RegisteredApp, key: str, before_scope: str, after_scope: str) -> tuple[str, str]:
    """For an app-wide scope change, the environments it does not reach:
    per-environment rows with a scope of their own that differs from the
    new one. Returned as the diff's ``overrides`` entry and a summary
    suffix, both empty when there are none. Without them, approving
    "production only" reads as covering every environment (#1758)."""
    if after_scope == before_scope:
        return "", ""
    kept = {env: scope for env, scope in environment_scopes(app, key).items() if scope != after_scope}
    return (
        ", ".join(f"{env} ({scope})" for env, scope in kept.items()),
        "".join(f"; {env} keeps {scope}" for env, scope in kept.items()),
    )


def build_diff(
    *,
    app: RegisteredApp,
    op: str,
    payload: dict[str, Any],
    environment_name: str,
) -> dict[str, Any]:
    """Render the before/after snapshot for the proposal at propose time.

    The diff is masked — full reveal requires the explicit
    ``revealAppSecret`` mutation which carries the disclosure audit
    trail (#424)."""
    raw = app.manifest_raw_staged or app.manifest_raw or ""
    literals = read_app_env(raw)
    env_display = environment_name or "(app-wide)"

    if op == SecretChangeProposal.Op.SET.value:
        key = payload.get("key") or ""
        new_value = payload.get("value") or ""
        before_value = literals.get(key, "")
        # Scope decides which environments get the value, so the approver
        # sees it, and any change to it, next to the value (#1758).
        before_scope = current_secret_scope(app, key)
        after_scope = payload.get("scope") or before_scope
        summary = f"Update {key} in {env_display}" if key in literals else f"Add {key} in {env_display}"
        if after_scope != before_scope:
            summary += f", scope {before_scope} to {after_scope}"
        overrides, kept_summary = _kept_scopes(app, key, before_scope, after_scope)
        after: dict[str, Any] = {
            "key": key,
            "value_masked": _mask(new_value),
            "present": True,
            "scope": after_scope,
        }
        if overrides:
            after["overrides"] = overrides
        return {
            "op": op,
            "before": {
                "key": key,
                "value_masked": _mask(before_value),
                "present": key in literals,
                "scope": before_scope,
            },
            "after": after,
            "summary": summary + kept_summary,
        }

    if op == SecretChangeProposal.Op.DELETE.value:
        key = payload.get("key") or ""
        before_value = literals.get(key, "")
        return {
            "op": op,
            "before": {
                "key": key,
                "value_masked": _mask(before_value),
                "present": key in literals,
            },
            "after": {
                "key": key,
                "value_masked": "",
                "present": False,
            },
            "summary": f"Delete {key} from {env_display}",
        }

    if op == SecretChangeProposal.Op.SET_METADATA.value:
        key = payload.get("key") or ""
        before_scope = current_secret_scope(app, key, environment_name)
        after_scope = payload.get("scope") or before_scope
        summary = f"Change scope of {key} in {env_display} from {before_scope} to {after_scope}"
        after = {"key": key, "scope": after_scope}
        if not environment_name:
            overrides, kept_summary = _kept_scopes(app, key, before_scope, after_scope)
            if overrides:
                after["overrides"] = overrides
            summary += kept_summary
        return {
            "op": op,
            "before": {"key": key, "scope": before_scope},
            "after": after,
            "summary": summary,
        }

    if op == SecretChangeProposal.Op.ATTACH_BUNDLE.value:
        bundle_slug = payload.get("bundle_slug") or ""
        prefix = payload.get("prefix") or ""
        return {
            "op": op,
            "before": {
                "bundle_slug": bundle_slug,
                "prefix": "",
                "present": False,
            },
            "after": {
                "bundle_slug": bundle_slug,
                "prefix": prefix,
                "present": True,
            },
            "summary": (
                f"Attach bundle {bundle_slug} to {env_display}"
                + (f" with prefix {prefix!r}" if prefix else "")
            ),
        }

    if op == SecretChangeProposal.Op.DETACH_BUNDLE.value:
        attachment_id = payload.get("attachment_id") or ""
        ref = AppSecretBundleRef.objects.filter(guid=attachment_id).first()
        bundle_slug = ref.secret_bundle.slug if ref is not None else attachment_id
        prefix = ref.prefix if ref is not None else ""
        return {
            "op": op,
            "before": {
                "bundle_slug": bundle_slug,
                "prefix": prefix or "",
                "present": True,
            },
            "after": {
                "bundle_slug": bundle_slug,
                "prefix": prefix or "",
                "present": False,
            },
            "summary": f"Detach bundle {bundle_slug} from {env_display}",
        }

    return {
        "op": op,
        "before": {},
        "after": {},
        "summary": f"{op} on {env_display}",
    }
