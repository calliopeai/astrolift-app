from __future__ import annotations

from typing import Optional

import strawberry
import strawberry_django
from strawberry.types import Info

from core.models.process import DataProcess, DataProcessEntity


def _scope_to_caller_org(queryset, info: Info, *, fk_path: str):
    """Filter ``queryset`` to rows whose ``fk_path`` resolves to the
    caller's active organization. Deny-by-default for anonymous and
    no-org callers; superusers bypass.

    #542 — DataProcess[Entity]Type used to return the unfiltered
    queryset (model-wide permission check only), which leaked every
    tenant's import rows to anyone with ``view_dataprocess``. The
    filter shape mirrors ``UploadQuerySet.with_view_permission``: the
    only material difference is the FK path because ``DataProcess``
    owns the column and ``DataProcessEntity`` traverses it.
    """
    user = info.context.user
    if user is None or not getattr(user, "is_authenticated", False):
        return queryset.none()
    if getattr(user, "is_superuser", False):
        return queryset

    profile = getattr(user, "profile", None)
    organization = profile.organization() if profile is not None else None
    if organization is None:
        return queryset.none()

    return queryset.filter(**{fk_path: organization})


@strawberry_django.type(DataProcessEntity)
class DataProcessEntityType:
    error_message: Optional[str]
    line_number: Optional[int]
    status: Optional[str]
    status_date: Optional[str]
    process: strawberry.ID

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return _scope_to_caller_org(queryset, info, fk_path="process__organization")


@strawberry_django.type(DataProcess)
class DataProcessType:
    gid: strawberry.ID
    file_type: Optional[str]

    @classmethod
    def get_queryset(cls, queryset, info: Info):
        return _scope_to_caller_org(queryset, info, fk_path="organization")

    @strawberry_django.field
    def rows(self, info: Info) -> list[DataProcessEntityType]:
        # Sibling DataProcessEntity rows belong to the same tenant by
        # construction (FK back to this DataProcess), but the parent
        # type's get_queryset already gated this DataProcess to the
        # caller's org, so we can safely list its rows.
        return DataProcessEntity.objects.filter(process=self)
