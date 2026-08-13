"""Generated MCP capability contract guardrails."""

from __future__ import annotations

import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from jsonschema.validators import Draft202012Validator

from astrolift_agents.mcp_contract import MCP_TOOL_META, mcp_contract_document
from astrolift_agents.views.mcp import _HANDLERS


def test_mcp_contract_matches_runtime_handlers_and_has_valid_input_schemas():
    assert set(MCP_TOOL_META) == set(_HANDLERS)
    for meta in MCP_TOOL_META.values():
        Draft202012Validator.check_schema(meta["inputSchema"])


def test_mcp_contract_is_a_serializable_capability_superset():
    document = mcp_contract_document()

    encoded = json.dumps(document, sort_keys=True)
    assert document["schema"] == "astrolift.mcp.capabilities/v1"
    assert document["authorization"]["rule"] == "token scope and user RBAC permission are both required"
    assert [tool["name"] for tool in document["tools"]] == list(MCP_TOOL_META)
    assert "agent.create" in encoded
    assert "mcp:write" in encoded


def test_export_contracts_writes_deterministically_and_detects_drift(tmp_path):
    graphql_path = tmp_path / "schema.graphql"
    mcp_path = tmp_path / "contracts" / "mcp-tools.json"
    options = {
        "graphql_output": str(graphql_path),
        "mcp_output": str(mcp_path),
    }

    call_command("export_contracts", **options)
    first = (graphql_path.read_bytes(), mcp_path.read_bytes())
    call_command("export_contracts", **options)
    assert (graphql_path.read_bytes(), mcp_path.read_bytes()) == first
    call_command("export_contracts", check=True, **options)

    mcp_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(CommandError, match="generated contracts are stale"):
        call_command("export_contracts", check=True, **options)
