"""
Soft-delete and tenant-scoped managers for Astrolift platform models.

Every BaseCoreModel exposes two managers:

* ``objects``       — default; hides rows where ``deleted_at`` is set.
* ``all_objects``   — administrative; returns all rows, including deleted.

The default manager is the one Django wires for related-set traversal,
so foreign-key joins also see only live rows. Admin and audit code paths
that need to inspect deleted rows go through ``all_objects`` explicitly.

Tenant scoping (``TenantScopedManager``) layers on top: it filters the
already-soft-delete-filtered queryset by the request-bound organization,
so accidental cross-tenant queries surface as empty results.
"""

from __future__ import annotations

from django.db import models


class SoftDeleteQuerySet(models.QuerySet):
    def alive(self) -> SoftDeleteQuerySet:
        return self.filter(deleted_at__isnull=True)

    def dead(self) -> SoftDeleteQuerySet:
        return self.filter(deleted_at__isnull=False)


class SoftDeleteManager(models.Manager):
    """Default manager: hides soft-deleted rows."""

    _queryset_class = SoftDeleteQuerySet

    def get_queryset(self) -> SoftDeleteQuerySet:
        return self._queryset_class(self.model, using=self._db).filter(deleted_at__isnull=True)


class UnscopedManager(models.Manager):
    """Administrative manager: returns every row, deleted or not."""

    _queryset_class = SoftDeleteQuerySet

    def get_queryset(self) -> SoftDeleteQuerySet:
        return self._queryset_class(self.model, using=self._db)


class TenantScopedQuerySet(SoftDeleteQuerySet):
    pass


class TenantScopedManager(SoftDeleteManager):
    """Filters by the current tenant context's organization_id.

    Models that opt into tenant scoping declare
    ``objects = TenantScopedManager()`` and a ``tenant_org_field``
    attribute pointing at the column reaching ``organization_id``
    (default: ``organization_id``).
    """

    _queryset_class = TenantScopedQuerySet

    def get_queryset(self) -> TenantScopedQuerySet:
        from core.tenancy import get_current_tenant

        qs = super().get_queryset()
        tenant = get_current_tenant()
        if tenant is None or tenant.organization_id is None:
            return qs
        field = getattr(self.model, "tenant_org_field", "organization_id")
        return qs.filter(**{field: tenant.organization_id})
