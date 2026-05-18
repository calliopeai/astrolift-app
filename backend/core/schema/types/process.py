from __future__ import annotations

from typing import Optional

import strawberry
import strawberry_django
from strawberry.types import Info

from core.models.process import DataProcess, DataProcessEntity
from core.schema.common import scope_to_caller_org as _scope_to_caller_org


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
