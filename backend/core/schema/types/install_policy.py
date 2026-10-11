"""What the install withholds and the model it serves, for the console.

Install-wide facts, the same for every org, set by the installer in the
control plane's environment (calliope-installer#447, #446). Authenticated
callers only; the shared model's internal endpoint is never returned.
"""

from __future__ import annotations

import strawberry
from strawberry.types import Info


@strawberry.type(name="AstroliftWithheldCapability")
class WithheldCapabilityType:
    """A capability the install withholds from Astrolift, with the reason to show."""

    capability: str
    """``dns``, ``databases``, ``load_balancers``, ``clusters`` or ``controllers``."""
    reason: str


@strawberry.type(name="AstroliftInstallManagedModel")
class InstallManagedModelType:
    """The open-weight model the install serves itself. Read-only in the console."""

    model_id: str
    replicas: int | None
    """Replicas the install reports, one GPU each; null when it does not say."""


def _require_viewer(info: Info, field: str) -> None:
    request = getattr(info.context, "request", None)
    viewer = getattr(request, "user", None) if request else None
    if viewer is None or not getattr(viewer, "is_authenticated", False):
        raise PermissionError(f"{field} requires an authenticated viewer")


@strawberry.type
class InstallPolicyQuery:
    @strawberry.field(
        description=(
            "What the install withholds from Astrolift (calliope-installer#447): dns, databases, "
            "load_balancers, clusters, controllers, each with the reason to show. Empty when nothing "
            "is withheld."
        )
    )
    def astrolift_withheld_capabilities(self, info: Info) -> list[WithheldCapabilityType]:
        """What the install withholds from Astrolift (calliope-installer#447).

        Empty on an install that withholds nothing, which is every install
        before the question. The console offers a withheld action disabled,
        with ``reason``.
        """
        _require_viewer(info, "astrolift_withheld_capabilities")
        from core.install_restrictions import CAPABILITIES, reason

        return [
            WithheldCapabilityType(capability=capability, reason=reason(capability))
            for capability in CAPABILITIES
            if reason(capability)
        ]

    @strawberry.field(
        description=(
            "The model the install serves on its own GPUs (calliope-installer#446), read-only in the "
            "console; null when the platform model is a cloud API or off."
        )
    )
    def astrolift_install_managed_model(self, info: Info) -> InstallManagedModelType | None:
        """The model the install serves on its own GPUs (calliope-installer#446), or null."""
        _require_viewer(info, "astrolift_install_managed_model")
        from astrolift_agents.services.platform_model import install_managed_model

        shared = install_managed_model()
        if shared is None:
            return None
        return InstallManagedModelType(model_id=shared.model, replicas=shared.replicas)
