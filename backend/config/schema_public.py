"""Curated install discovery before a client has authenticated."""

import strawberry

from core.schema.types.server_info import AstroliftServerInfoQuery

schema_public = strawberry.Schema(query=AstroliftServerInfoQuery)
