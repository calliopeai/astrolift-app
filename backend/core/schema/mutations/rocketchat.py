"""Rocket.Chat mutations migrated from Graphene to Strawberry."""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from core.utils.api.rocketchat_rest_client import RocketchatRestClient


@strawberry.type
class RocketchatMutations:
    @strawberry.mutation(
        description="Generate a temporary authentication token for Rocket.Chat. "
        "TTL is configured on the Rocket.Chat server."
    )
    def generate_rocket_chat_token(self, info: Info) -> str:
        from core.schema.legacy_access import require_account_access

        require_account_access(info, write=True)
        client = RocketchatRestClient()
        return client.create_auth_token(info.context.user)
