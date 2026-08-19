"""Every Azure ownership decision has to route through the one verifier (#1365).

The bug this pins is not a wrong line of code, it is drift: each driver used to
carry its own ``_assert_owned``, the copies disagreed about what "owned" means,
and the weakest copy set the real safety floor for destructive operations. A
reviewer cannot see that by reading one driver, so it is asserted here instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from _sdk.azure_ownership import METADATA_KEYS
from azure.managed import filesystem_files_classic

MANAGED = Path(filesystem_files_classic.__file__).parent
#: ``adoption.py`` is excluded with the tag helper because it is not a driver:
#: it is the authorized adoption operation's cloud half, deliberately outside
#: the lifecycle so no driver path can reach it (#1365). Its own guard test
#: asserts that separation from the other direction.
DRIVERS = sorted(
    path for path in MANAGED.glob("*.py") if path.name not in {"__init__.py", "tags.py", "adoption.py"}
)


def _assertion_functions(tree: ast.AST) -> list[ast.FunctionDef]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and "owned" in node.name and node.name.startswith("_assert")
    ]


def _calls(node: ast.AST) -> set[str]:
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            names.add(func.id if isinstance(func, ast.Name) else getattr(func, "attr", ""))
    return names


@pytest.mark.parametrize("path", DRIVERS, ids=lambda path: path.stem)
def test_no_driver_decides_ownership_on_its_own(path: Path) -> None:
    tree = ast.parse(path.read_text())
    for function in _assertion_functions(tree):
        calls = _calls(function)
        assert "verify_azure_ownership" in calls or calls & {"_assert_owned", "_assert_account_owned"}, (
            f"{path.name}::{function.name} decides ownership without the shared verifier; "
            f"call verify_azure_ownership so one rule governs every destructive path"
        )


def test_at_least_the_known_drivers_are_covered() -> None:
    """A rename that empties the scan would make the guard above vacuously pass."""

    migrated = {path.stem for path in DRIVERS if "verify_azure_ownership" in path.read_text()}
    assert migrated >= {
        "api_management",
        "cache_redis",
        "cosmos",
        "cosmos_api",
        "email_acs",
        "event_grid",
        "event_grid_namespace",
        "event_hubs",
        "faas_functions",
        "filesystem_files",
        "filesystem_files_classic",
        "managed_redis",
        "model_endpoint_aoai",
        "mssql_sql",
        "mysql_flexible",
        "object_store_blob",
        "postgres_flexible",
        "private_endpoint",
        "queue_servicebus",
        "search_aisearch",
        "timeseries_monitor",
        "vector_search",
    }


def test_every_lifecycle_driver_is_accounted_for() -> None:
    """The scan is only a floor if nothing can quietly sit outside it.

    ``email_obs`` is the one module under ``azure/managed`` that owns no cloud
    resource: every method on it raises ``UnsupportedOperationError``, so it
    provisions nothing to prove ownership of. Anything else appearing here is a
    driver that reached main without an ownership gate.
    """

    ungated = {path.stem for path in DRIVERS if "verify_azure_ownership" not in path.read_text()}
    assert ungated == {"email_obs"}


def test_the_classic_share_metadata_write_path_uses_the_keys_the_gate_reads() -> None:
    """Share ownership lives in file-share metadata, whose key vocabulary is not
    the ARM tag vocabulary. A driver that writes one and reads the other decides
    it does not own its own share."""

    source = Path(filesystem_files_classic.__file__).read_text()
    for key in (METADATA_KEYS.managed_by, METADATA_KEYS.managed_service_id, METADATA_KEYS.binding):
        assert f'"{key}"' in source, f"classic Azure Files never writes {key}"
