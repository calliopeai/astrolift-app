"""
OrganizationAllowlistedDomain — trusted email domain on an Organization.

A successful IdP login whose email lives under a row here will
auto-create a Member for the org. With ``requires_review=True`` the
Member lands as ``pending_invite`` and an admin must approve it
before the user gets active access; with ``requires_review=False``
the Member is active immediately.

This is intentionally separate from the dormant ``OrgDomain`` model
which carries different semantics (``jit_enabled`` toggle, globally
unique domain). The allowlist scopes uniqueness *per organization*
so the same corp domain can be allowlisted across orgs without one
org's row blocking another.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class OrganizationAllowlistedDomain(BaseCoreModel):
    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="allowlisted_domains",
        on_delete=models.CASCADE,
    )
    # Stored lowercased; the model save() and CRUD mutation both
    # normalize on write. Indexed for O(log n) lookup on every OIDC
    # callback.
    domain = models.CharField(max_length=255, db_index=True)

    # Optional — when set, the auto-created Member also receives a
    # RoleBinding to this role at the org scope. NULL leaves the
    # default-role decision to whatever policy the org operator
    # configures elsewhere (and the Member lands without any binding).
    default_role = models.ForeignKey(
        "astrolift_identity.Role",
        related_name="allowlisted_domain_defaults",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    # True ⇒ auto-create the Member in ``pending_invite`` so an admin
    # has to flip it active before the user can use the org. False
    # ⇒ the Member is active immediately on first successful sign-in.
    requires_review = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "domain"],
                condition=models.Q(deleted_at__isnull=True),
                name="oad_unique_org_domain_active",
            ),
        ]
        indexes = [
            models.Index(
                fields=["domain", "organization"],
                name="oad_lookup_domain_org_idx",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover — admin display only
        return f"{self.domain} → {self.organization_id}"

    def save(self, *args, **kwargs):
        if self.domain:
            self.domain = self.domain.strip().lower()
        super().save(*args, **kwargs)
