"""Core-loop API surface smoke (#976).

The core-loop mutations/queries each have per-resolver coverage, but the root
Query/Mutation are assembled by multiple-inheritance over feature-gated app
bases (config/schema.py). If a base fails to mount (feature gating) or a field
is dropped/renamed, the individual unit tests still pass while the *assembled*
schema silently loses the field. This pins that the assembled schema exposes
the whole core loop.

Callability of each field is proven elsewhere: deploy via
test_cli_deploy_api, run_workflow_definition via
test_run_workflow_definition_mutation, agent dispatch via
astrolift_agents/tests/test_run_agent_mutation.
"""

from __future__ import annotations

import re

CORE_LOOP_MUTATIONS = [
    "registerApp",
    "startDeployment",
    "deregisterAstroliftApp",
    "runAstroliftAgent",
    "runWorkflowDefinition",
]
CORE_LOOP_QUERIES = [
    "astroliftDeployment",
    "astroliftDeployments",
]


def _sdl() -> str:
    # Import lazily so a schema-assembly error surfaces as a test failure,
    # not a collection error.
    from config.schema import schema

    return str(schema)


def test_assembled_schema_exposes_core_loop_mutations():
    sdl = _sdl()
    missing = [m for m in CORE_LOOP_MUTATIONS if not re.search(rf"\b{m}\b", sdl)]
    assert not missing, f"core-loop mutations missing from assembled schema: {missing}"


def test_assembled_schema_exposes_core_loop_queries():
    sdl = _sdl()
    missing = [q for q in CORE_LOOP_QUERIES if not re.search(rf"\b{q}\b", sdl)]
    assert not missing, f"core-loop queries missing from assembled schema: {missing}"
