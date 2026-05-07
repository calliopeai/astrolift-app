"""
Cross-cutting model mixins for the Astrolift platform.

These are abstract Django models that compose into ``BaseCoreModel``;
they are also reusable on their own when a model can't fit the full
``BaseCoreModel`` shape (e.g. append-only logs).
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class TrackingMixin(models.Model):
    """Audit columns: created/updated/deleted by/at + version.

    ``version`` is incremented on every ``save()`` and is the basis for
    optimistic-concurrency control: callers compare the version they
    fetched against the version they're about to write, and abort the
    update on a mismatch (see ``BaseCoreModel.save_with_version_check``).
    """

    class Meta:
        abstract = True

    version = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Creator",
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class TenantScopedMixin(models.Model):
    """Marks a model as belonging to an organization.

    The concrete FK is declared on the model itself (so the spec's
    "organization_id is denormalized on Project" is honored). This mixin
    only sets ``tenant_org_field``, which the manager reads.
    """

    class Meta:
        abstract = True

    tenant_org_field: str = "organization_id"


class AppendOnlyMixin(models.Model):
    """Refuses ``UPDATE`` and ``DELETE`` at the application layer.

    Database-level enforcement (triggers / rules) is added in a
    follow-up migration; this mixin gives us the same guarantee for
    code paths that go through the ORM today.
    """

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError(
                f"{type(self).__name__} is append-only; rows cannot be updated"
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError(
            f"{type(self).__name__} is append-only; rows cannot be deleted"
        )


def utc_now():
    return timezone.now()
