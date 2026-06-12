"""Strawberry GraphQL schema for the agent platform.

Composes into the root schema in ``config/schema.py`` as
``AgentsQuery`` / ``AgentsMutation``.
"""

from astrolift_agents.schema.mutations import AgentsMutation
from astrolift_agents.schema.queries import AgentsQuery

__all__ = ["AgentsMutation", "AgentsQuery"]
