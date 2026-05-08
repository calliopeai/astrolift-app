"""
Astrolift platform BaseCoreModel.

The canonical base for every business entity in the control plane.
See ``specs/04-app-registry-data-model.md`` §1 (Modeling Principles)
and §11 (Soft-Delete Semantics).

Every business entity inherits from ``BaseCoreModel``:

* integer ``id`` PK (internal optimization, never surfaced via API)
* ``guid`` UUID v7 (public identifier, time-ordered)
* tracking columns: ``created_at/by``, ``updated_at/by``, ``deleted_at/by``,
  ``version``
* default manager filters soft-deleted rows; ``all_objects`` returns them

Slug uniqueness is *partial*: scoped to live rows so a deleted slug can
be re-claimed.

The model is intentionally separate from the legacy
``core.models.common.BaseCoreModel`` (the boilerworks scaffold). New
models import ``from core.models.base import BaseCoreModel``; legacy
models keep their existing imports.
"""

from __future__ import annotations

from django.db import models
from django.utils.text import slugify

from core.fields import UUIDv7Field
from core.managers import SoftDeleteManager, UnscopedManager
from core.mixins import TrackingMixin


class BaseCoreModel(TrackingMixin):
    """Shared abstract base for every Astrolift business entity."""

    objects = SoftDeleteManager()
    all_objects = UnscopedManager()
    _unscoped = UnscopedManager()

    guid = UUIDv7Field(unique=True, db_index=True)

    class Meta:
        abstract = True

    def soft_delete(self, *, by=None) -> None:
        from django.utils import timezone

        self.deleted_at = timezone.now()
        if by is not None:
            self.deleted_by = by
        self.save(update_fields=["deleted_at", "deleted_by", "updated_at", "version"])

    def restore(self) -> None:
        self.deleted_at = None
        self.deleted_by = None
        self.save(update_fields=["deleted_at", "deleted_by", "updated_at", "version"])

    def save(self, *args, **kwargs):
        self.version = (self.version or 0) + 1
        super().save(*args, **kwargs)

    def save_with_version_check(self, *, expected_version: int) -> None:
        """Optimistic-concurrency save.

        Raises ``OptimisticLockError`` if the row was modified between
        the caller's read and write.
        """
        from django.db import transaction

        with transaction.atomic():
            current = (
                type(self)
                .all_objects.select_for_update()
                .filter(pk=self.pk)
                .values_list("version", flat=True)
            )
            current_version = next(iter(current), None)
            if current_version is None:
                raise OptimisticLockError(f"{type(self).__name__}(pk={self.pk}) has been deleted")
            if current_version != expected_version:
                raise OptimisticLockError(
                    f"{type(self).__name__}(pk={self.pk}) "
                    f"version mismatch: expected {expected_version}, got {current_version}"
                )
            self.save()


class NamedBaseCoreModel(BaseCoreModel):
    """BaseCoreModel for entities with a human-readable name + slug.

    Most user-visible entities (Org, Team, Project, App, ...) extend this.
    Slug uniqueness is enforced via a partial index on
    ``(<scope>, slug) WHERE deleted_at IS NULL`` declared on each
    concrete subclass.
    """

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, db_index=True)
    description = models.TextField(blank=True, default="")

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.slug and self.name:
            self.slug = slugify(self.name)[:200]
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class OptimisticLockError(RuntimeError):
    """Raised when ``save_with_version_check`` detects a concurrent write."""


__all__ = [
    "BaseCoreModel",
    "NamedBaseCoreModel",
    "OptimisticLockError",
]
