from __future__ import annotations

from typing import Optional

import strawberry_django
from strawberry.types import Info

from core.models import Address
from core.permissions import is_platform_operator


@strawberry_django.type(Address)
class AddressType:
    """Address whose fields only the platform operator sees.

    Each field used to check a Django field permission, which Astrolift never
    grants, so only superusers saw them; that is now said directly (#1864).
    """

    @strawberry_django.field
    def address_line_one(self, info: Info) -> str:
        if is_platform_operator(info.context.user):
            return self.address_line_one
        return ""

    @strawberry_django.field
    def address_line_two(self, info: Info) -> str:
        if is_platform_operator(info.context.user):
            return self.address_line_two
        return ""

    @strawberry_django.field
    def city(self, info: Info) -> str:
        if is_platform_operator(info.context.user):
            return self.city
        return ""

    @strawberry_django.field
    def state(self, info: Info) -> str:
        if is_platform_operator(info.context.user):
            return self.state
        return ""

    @strawberry_django.field
    def street(self, info: Info) -> str:
        if is_platform_operator(info.context.user):
            return self.street
        return ""

    @strawberry_django.field
    def zipcode(self, info: Info) -> str:
        if is_platform_operator(info.context.user):
            return self.zipcode
        return ""
