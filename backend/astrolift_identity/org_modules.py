"""Per-organization module state (#1859).

A module such as the Chat Studio integration is install-wide code but a
per-organization decision. Two switches decide whether it is on for an org:

* the install admin's Constance switch, on by default, which forces the
  module off for every org when turned off (the platform-admin feature
  flipper already manages these, so it doubles as the kill switch the AHP
  fallback contract asks for, calliopeai/calliope-vscode#796);
* the org admin's ``OrganizationModule`` row, off until they turn it on.

This is the only place the two are combined, so ``me.modules``, the builder
API gate and the AHP attach relay (#1860) cannot disagree about a module.
"""

from __future__ import annotations

from astrolift_identity.models import OrganizationModule

CHAT_STUDIO_INTEGRATION = str(OrganizationModule.Key.CHAT_STUDIO_INTEGRATION)
AGENT_LIVE_ATTACH = str(OrganizationModule.Key.AGENT_LIVE_ATTACH)

# Module key -> the Constance switch the install admin turns off to force
# the module off for every organization.
INSTALL_SWITCHES: dict[str, str] = {
    CHAT_STUDIO_INTEGRATION: "CHAT_STUDIO_INTEGRATION_ALLOWED",
    AGENT_LIVE_ATTACH: "AGENT_LIVE_ATTACH_ALLOWED",
}

# Why a module is off. Distinct because the fix differs: an org admin can
# turn on a module the org has not enabled, but only the install admin can
# lift an install-wide force-off.
REASON_DISABLED_BY_INSTALL = "module_disabled_by_install"
REASON_NOT_ENABLED = "module_not_enabled"


def allowed_by_install(key: str) -> bool:
    from constance import config as constance_config

    return bool(getattr(constance_config, INSTALL_SWITCHES[key]))


def module_state(organization_id: int | None, key: str) -> tuple[bool, str | None]:
    """``(enabled, reason)`` for one module in one org; ``reason`` is None when on."""
    if not allowed_by_install(key):
        return False, REASON_DISABLED_BY_INSTALL
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
    return frozenset(key for key in INSTALL_SWITCHES if key in org_on and allowed_by_install(key))
