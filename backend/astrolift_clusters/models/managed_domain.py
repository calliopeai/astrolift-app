"""
ManagedDomain — DNS zone owned by the platform or a customer.

The platform manages records under a managed domain on behalf of
tenant apps. ``default_for`` selects whether this domain is the default
for tenant apps, preview environments, both, or none.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q

from core.models.base import BaseCoreModel


class ManagedDomain(BaseCoreModel):
    class DefaultFor(models.TextChoices):
        TENANT_APPS = "tenant_apps"
        PREVIEW_ENVS = "preview_envs"
        BOTH = "both"
        NONE = "none"

    zone = models.CharField(max_length=255)
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="managed_domains",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
    )
    dns_driver = models.CharField(max_length=64)
    dns_config = models.JSONField(default=dict, blank=True)
    provision_state = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=(
            "Current ZoneRegistrationStep. Empty = not started; " "'mark_active' = fully provisioned."
        ),
    )
    provision_nameservers = models.JSONField(
        default=list,
        blank=True,
        help_text="NS records returned when the zone was created. Shown to operator for registrar delegation.",
    )
    provision_cert_id = models.CharField(
        max_length=512,
        blank=True,
        default="",
        help_text="Cloud cert ID/ARN — used for poll and reissue operations.",
    )
    provision_zone_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "Hosted zone the platform created for this row. Platform-written only, never from dns_config "
            "(tenant-editable): DNS writes pin to it, and teardown deletes only it (#1931)."
        ),
    )
    provision_validation_records = models.JSONField(
        default=list,
        blank=True,
        help_text="DNS validation CNAME records the operator must add (cloud cert DNS-01 challenge).",
    )
    default_for = models.CharField(
        max_length=32,
        choices=DefaultFor.choices,
        default=DefaultFor.NONE,
    )
    is_wildcard_managed = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["zone"],
                condition=models.Q(deleted_at__isnull=True),
                name="managed_domain_zone_unique_active",
            ),
        ]


def resolve_managed_domain(
    organization: object | None,
    for_preview: bool = False,
) -> ManagedDomain | None:
    """Return the best ManagedDomain for a new AppEnvironment.

    Resolution order:
    1. ``organization.default_managed_domain`` when set and not deleted.
    2. An **org-scoped** domain belonging to ``organization`` whose
       ``default_for`` covers the requested use-case.
    3. A platform-level domain (``organization=None``) whose
       ``default_for`` covers the requested use-case.
    4. ``None`` — no managed domain; the env gets no platform hostname.

    Step 2 is the one that makes the supported path work (#1714).
    ``createManagedDomain`` defaults to ``organizationScoped=true``, and
    nothing anywhere writes ``organization.default_managed_domain`` — no
    mutation, no command — so before this an operator who registered a
    zone through the UI got a row that was accepted, listed, and matched
    by nothing, and their apps still came up with no hostname. That is the
    same defect #1689 fixed for ``default_for``, one level down.

    An org's own domain outranks the platform default: an install that
    offers a shared zone should not override the zone an org registered
    for itself.

    ``for_preview=True`` matches ``preview_envs`` and ``both``;
    ``for_preview=False`` (default) matches ``tenant_apps`` and ``both``.
    """
    target_values = (
        [ManagedDomain.DefaultFor.PREVIEW_ENVS, ManagedDomain.DefaultFor.BOTH]
        if for_preview
        else [ManagedDomain.DefaultFor.TENANT_APPS, ManagedDomain.DefaultFor.BOTH]
    )
    if organization is not None:
        org_default = getattr(organization, "default_managed_domain", None)
        if org_default is not None and getattr(org_default, "deleted_at", None) is None:
            return org_default  # type: ignore[return-value]
        org_pk = getattr(organization, "pk", None)
        if org_pk is not None:
            own = (
                ManagedDomain.objects.filter(
                    organization_id=org_pk,
                    default_for__in=target_values,
                    deleted_at__isnull=True,
                )
                .order_by("pk")
                .first()
            )
            if own is not None:
                return own

    return (
        ManagedDomain.objects.filter(
            organization__isnull=True,
            default_for__in=target_values,
            deleted_at__isnull=True,
        )
        .order_by("pk")
        .first()
    )


def managed_domain_for_zone(zone: str, organization_id: int | None) -> ManagedDomain | None:
    """The active zone ``organization_id`` may act on by this name, else ``None``.

    Only the org's own zones and shared (org NULL) ones resolve. Zone names
    are guessable and unique across the install, so another org's zone
    answers ``None``, exactly like a zone nobody registered (#1909). With no
    org the shared branch alone would match, so that answers ``None`` too.
    """
    if organization_id is None:
        return None
    return ManagedDomain.objects.filter(
        Q(organization_id=organization_id) | Q(organization__isnull=True),
        zone=zone,
        deleted_at__isnull=True,
    ).first()
