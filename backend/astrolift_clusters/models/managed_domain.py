"""
ManagedDomain — DNS zone owned by the platform or a customer.

The platform manages records under a managed domain on behalf of
tenant apps. ``default_for`` selects whether this domain is the default
for tenant apps, preview environments, both, or none.
"""

from __future__ import annotations

from django.db import models

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
    2. A platform-level domain (``organization=None``) whose
       ``default_for`` covers the requested use-case.
    3. ``None`` — no managed domain; the env gets no platform hostname.

    ``for_preview=True`` matches ``preview_envs`` and ``both``;
    ``for_preview=False`` (default) matches ``tenant_apps`` and ``both``.
    """
    if organization is not None:
        org_default = getattr(organization, "default_managed_domain", None)
        if org_default is not None and getattr(org_default, "deleted_at", None) is None:
            return org_default  # type: ignore[return-value]

    target_values = (
        [ManagedDomain.DefaultFor.PREVIEW_ENVS, ManagedDomain.DefaultFor.BOTH]
        if for_preview
        else [ManagedDomain.DefaultFor.TENANT_APPS, ManagedDomain.DefaultFor.BOTH]
    )
    return (
        ManagedDomain.objects.filter(
            organization__isnull=True,
            default_for__in=target_values,
            deleted_at__isnull=True,
        )
        .order_by("pk")
        .first()
    )
