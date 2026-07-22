"""
Soft-delete and tenant-scoped managers for Astrolift platform models.

Every BaseCoreModel exposes two managers:

* ``objects``       — default; hides rows where ``deleted_at`` is set.
* ``all_objects``   — administrative; returns all rows, including deleted.

The default manager is the one Django wires for related-set traversal,
so foreign-key joins also see only live rows. Admin and audit code paths
that need to inspect deleted rows go through ``all_objects`` explicitly.

``TenantScopedManager`` below WOULD layer org filtering on top — but it is
wired on ZERO models today. The default ``objects`` manager therefore
filters by soft-delete ONLY, never by organization. Do NOT assume any query
is tenant-scoped by the manager layer; see its docstring (#1183).
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
    """Would filter by the current tenant context's ``organization_id``.

    .. warning::
       NOT WIRED ON ANY MODEL. No model declares
       ``objects = TenantScopedManager()``, so this class is dormant and
       provides ZERO isolation in practice. Do NOT rely on it — or on the
       default ``objects`` manager — to scope by organization: a bare
       ``Model.objects.get(pk=...)`` / ``.filter(guid=...)`` returns rows
       from ANY org. Every resolver MUST add its own explicit
       ``organization_id=`` constraint and fail closed on a missing tenant
       context. Trusting this dormant manager to isolate is exactly what
       produced the #1183 leak class.

    If a model ever does opt in, it declares
    ``objects = TenantScopedManager()`` and a ``tenant_org_field`` attribute
    pointing at the column reaching ``organization_id``
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
