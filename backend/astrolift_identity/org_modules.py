"""Per-organization module state (#1859).

A module such as the Chat Studio integration is install-wide code but a
per-organization decision. Two switches decide whether it is on for an org:

* the install admin's Constance switch, on by default, which forces the
  module off for every org when turned off (the platform-admin feature
  flipper already manages these, so it doubles as the kill switch the AHP
  fallback contract asks for, calliopeai/calliope-vscode#796);
* the install's signed licence, when it carries the modules claim
  (``astrolift_identity.licence``, #1879): a module it withholds is off for
  every org, whatever the org admin set. A grant only allows a module; it
  never turns one on;
* the org admin's ``OrganizationModule`` row, off until they turn it on.

This is the only place they are combined, so ``me.modules``, the builder
API gate and the AHP attach relay (#1860) cannot disagree about a module.
"""

from __future__ import annotations

from astrolift_identity.licence import licence_fingerprint, licensed_modules
from astrolift_identity.models import OrganizationModule

CHAT_STUDIO_INTEGRATION = str(OrganizationModule.Key.CHAT_STUDIO_INTEGRATION)
AGENT_LIVE_ATTACH = str(OrganizationModule.Key.AGENT_LIVE_ATTACH)
AGENT_POLICY_ENFORCEMENT = str(OrganizationModule.Key.AGENT_POLICY_ENFORCEMENT)
CHAT_STUDIO_AGENT_RUNS = str(OrganizationModule.Key.CHAT_STUDIO_AGENT_RUNS)

# Module key -> the Constance switch the install admin turns off to force
# the module off for every organization.
INSTALL_SWITCHES: dict[str, str] = {
    CHAT_STUDIO_INTEGRATION: "CHAT_STUDIO_INTEGRATION_ALLOWED",
    AGENT_LIVE_ATTACH: "AGENT_LIVE_ATTACH_ALLOWED",
    AGENT_POLICY_ENFORCEMENT: "AGENT_POLICY_ENFORCEMENT_ALLOWED",
    CHAT_STUDIO_AGENT_RUNS: "CHAT_STUDIO_AGENT_RUNS_ALLOWED",
}

# Why a module is off. Distinct because the fix differs: an org admin can
# turn on a module the org has not enabled, but only the install admin can
# lift an install-wide force-off.
REASON_DISABLED_BY_INSTALL = "module_disabled_by_install"
REASON_NOT_ENABLED = "module_not_enabled"
REASON_NOT_LICENSED = "module_not_licensed"


def allowed_by_install(key: str) -> bool:
    from constance import config as constance_config

    return bool(getattr(constance_config, INSTALL_SWITCHES[key]))


def withheld_by_licence(key: str) -> bool:
    """True when the licence speaks to modules and does not grant this one."""
    granted = licensed_modules()
    return granted is not None and key not in granted


def module_state(organization_id: int | None, key: str) -> tuple[bool, str | None]:
    """``(enabled, reason)`` for one module in one org; ``reason`` is None when on."""
    if not allowed_by_install(key):
        return False, REASON_DISABLED_BY_INSTALL
    if withheld_by_licence(key):
        return False, REASON_NOT_LICENSED
    if not OrganizationModule.objects.filter(organization_id=organization_id, key=key, enabled=True).exists():
        return False, REASON_NOT_ENABLED
    return True, None


def enabled_modules(organization_id: int | None) -> frozenset[str]:
    """Every module key that is on for the org, for ``me.modules``."""
    org_on = set(
        OrganizationModule.objects.filter(organization_id=organization_id, enabled=True).values_list(
            "key", flat=True
        )
    )
    return frozenset(
        key
        for key in INSTALL_SWITCHES
        if key in org_on and allowed_by_install(key) and not withheld_by_licence(key)
    )


def module_sources(organization_id: int | None) -> dict[str, str]:
    """Who last set each module the org has a row for: ``org_admin`` or ``licence``."""
    return dict(
        OrganizationModule.objects.filter(organization_id=organization_id).values_list("key", "source")
    )


def sync_from_licence() -> list[tuple[int, str, bool]]:
    """Turn off, in every org, each module the licence withholds; ``(org_id, key, False)`` per change.

    Nothing happens when the licence has no say. A grant only allows a module:
    it never turns one on, so a module stays off until the org admin turns it
    on (#1859). A withheld module that is on is turned off with
    ``source=licence``, and the org admin cannot turn it back on while the
    licence withholds it. When a later licence grants it again it stays off
    until the org admin turns it on. Each change is audited.
    """
    from core.mutations import AuditEntry, emit_audit

    granted = licensed_modules()
    if granted is None:
        return []
    withheld = [key for key in INSTALL_SWITCHES if key not in granted]
    changes = []
    for row in OrganizationModule.objects.filter(
        key__in=withheld, enabled=True, organization__deleted_at__isnull=True
    ).order_by("organization_id", "key"):
        row.enabled = False
        row.source = OrganizationModule.Source.LICENCE
        row.save()
        changes.append((row.organization_id, row.key, False))
        emit_audit(
            AuditEntry(
                actor_user_id=None,
                organization_id=row.organization_id,
                action="org.module.licence",
                decision="ALLOW",
                target_kind="OrganizationModule",
                target_id=row.key,
                duration_ms=0,
                permissions=(),
                extra={"enabled": False, "licence": licence_fingerprint()},
            )
        )
    return changes
