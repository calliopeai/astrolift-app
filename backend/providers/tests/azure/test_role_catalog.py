"""Tests for the Azure grant contract (#1367)."""

from __future__ import annotations

import ast
import pathlib

import pytest

from azure import role_catalog
from azure.role_catalog import (
    AZURE_BUILTIN_ROLE_IDS,
    AZURE_GRANT_ACTIONS,
    AzureGrantContractError,
    ControlPlaneGrant,
    RoleGrant,
    resolve_grant_action,
    role_definition_resource_id,
    validate_arm_scope,
)

_MANAGED_DIR = pathlib.Path(role_catalog.__file__).parent / "managed"


def _declared_grant_actions(module: pathlib.Path) -> set[str]:
    """Every action string the module's ``Grant(...)`` calls can carry.

    Literal lists are read straight off the call; a list built up in a local
    (``roles = [...]`` / ``roles.append(...)``) is resolved by collecting the
    strings bound to that name anywhere in the module.
    """
    tree = ast.parse(module.read_text())
    by_name: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            target, value = node.target.id, node.value
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"append", "extend"}
            and isinstance(node.func.value, ast.Name)
        ):
            target, value = node.func.value.id, node.args[0] if node.args else None
        if target is None or value is None:
            continue
        by_name.setdefault(target, set()).update(_constant_strings(value))

    actions: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Grant"):
            continue
        arg = node.args[1] if len(node.args) >= 2 else None
        for keyword in node.keywords:
            if keyword.arg == "actions":
                arg = keyword.value
        if arg is None:
            continue
        actions.update(_constant_strings(arg))
        if isinstance(arg, ast.Name):
            actions.update(by_name.get(arg.id, set()))
    return actions


def _constant_strings(node: ast.AST) -> set[str]:
    return {child.value for child in ast.walk(node) if isinstance(child, ast.Constant) and isinstance(child.value, str)}


def test_every_declared_driver_grant_is_classified():
    """The regression guard: a new driver grant with no catalog row must fail
    here rather than silently granting the workload nothing."""
    unclassified: dict[str, str] = {}
    for module in sorted(_MANAGED_DIR.glob("*.py")):
        for action in sorted(_declared_grant_actions(module)):
            try:
                resolve_grant_action(action)
            except AzureGrantContractError:
                unclassified[action] = module.name
    assert unclassified == {}


def test_role_grants_point_at_vetted_builtin_guids():
    for action, classified in AZURE_GRANT_ACTIONS.items():
        if isinstance(classified, ControlPlaneGrant):
            assert classified.reason, action
            continue
        assert AZURE_BUILTIN_ROLE_IDS[classified.role_name] == classified.role_definition_guid, action


def test_builtin_role_guids_are_unique():
    guids = list(AZURE_BUILTIN_ROLE_IDS.values())
    assert len(set(guids)) == len(guids)


def test_display_name_resolves_to_builtin_guid():
    assert resolve_grant_action("Storage Blob Data Contributor") == RoleGrant(
        "Storage Blob Data Contributor",
        "ba92f5b4-2d11-453d-a403-e96b0029c9fe",
    )


def test_bare_guid_and_full_role_definition_path_resolve_identically():
    guid = AZURE_BUILTIN_ROLE_IDS["Azure Service Bus Data Sender"]
    path = f"/subscriptions/sub-1/providers/Microsoft.Authorization/roleDefinitions/{guid}"
    assert resolve_grant_action(guid) == resolve_grant_action(path)


def test_unvetted_guid_fails_closed():
    with pytest.raises(AzureGrantContractError, match="vetted built-in catalog"):
        resolve_grant_action("00000000-0000-0000-0000-000000000001")


def test_unknown_management_action_fails_closed():
    with pytest.raises(AzureGrantContractError, match="granting the workload nothing"):
        resolve_grant_action("Microsoft.Storage/storageAccounts/blobServices/read")


def test_keyvault_secret_read_is_control_plane_not_a_workload_role():
    # The pod reads this material from its projected Secret, so assigning the
    # workload a Key Vault role for it would be pure over-permission.
    classified = resolve_grant_action("Microsoft.KeyVault/vaults/secrets/getSecret")
    assert isinstance(classified, ControlPlaneGrant)


def test_openai_data_action_maps_to_the_keyless_data_plane_role():
    classified = resolve_grant_action(
        "Microsoft.CognitiveServices/accounts/OpenAI/deployments/action",
    )
    assert classified == RoleGrant(
        "Cognitive Services OpenAI User",
        AZURE_BUILTIN_ROLE_IDS["Cognitive Services OpenAI User"],
    )


@pytest.mark.parametrize(
    "scope",
    [
        "/subscriptions/sub-1",
        "/subscriptions/sub-1/resourceGroups/rg",
        (
            "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Storage"
            "/storageAccounts/acct/blobServices/default/containers/data"
        ),
    ],
)
def test_valid_arm_scopes_pass(scope):
    assert validate_arm_scope(scope) == scope


@pytest.mark.parametrize(
    "scope",
    [
        "astrolift-managed-redis-cache-primary",
        "arn:aws:s3:::bucket",
        "/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/a",
        "",
    ],
)
def test_non_arm_scopes_fail_closed(scope):
    with pytest.raises(AzureGrantContractError, match="ARM resource scope"):
        validate_arm_scope(scope)


def test_role_definition_resource_id_is_subscription_qualified():
    assert role_definition_resource_id(
        subscription_id="sub-1",
        role_definition_guid="ba92f5b4-2d11-453d-a403-e96b0029c9fe",
    ) == ("/subscriptions/sub-1/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe")
