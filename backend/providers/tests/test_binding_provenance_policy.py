"""The rule for when a binding value must be a reference (#1410).

`_VALUE_PROVENANCE_DIVERGENCE` in the envelope-contract test records ten keys
that arrive as a secrets-backend reference from one driver and a literal from
another, each with a stated reason. A ledger with reasons is better than
nothing, and it is still a list of exceptions to a rule nobody wrote down: it
cannot say whether an eleventh entry is legitimate.

`_sdk/binding_policy` is that rule. These tests check two things: that the rule
explains every row already in the ledger, which is the evidence it describes the
drivers rather than replacing them, and that it refuses the two mistakes it
exists to prevent.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from _sdk.binding_policy import (
    LITERAL,
    OPERATOR_GENERATED_DRIVERS,
    PASS_THROUGH_REFERENCES,
    SECRET_REF,
    KeyClass,
    allowed_provenance,
    classify_key,
    violation,
)

_CONTRACT = pathlib.Path(__file__).with_name("test_binding_envelope_contract.py")


def _ledger() -> list[tuple[str, str, str, str]]:
    """(kind, key, provenance, driver) rows, parsed rather than imported.

    Importing the contract module drags in its collection machinery; the ledger
    is a literal and reading it with ``ast`` keeps this test independent of it.
    """
    tree = ast.parse(_CONTRACT.read_text(encoding="utf-8"))
    node = next(
        n.value
        for n in tree.body
        if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "_VALUE_PROVENANCE_DIVERGENCE"
    )

    def drivers(entry: ast.expr) -> set[str]:
        first = entry.elts[0]
        if isinstance(first, ast.Call) and getattr(first.func, "id", "") == "frozenset":
            return {ast.literal_eval(e) for e in first.args[0].elts}
        return set(ast.literal_eval(first))

    rows: list[tuple[str, str, str, str]] = []
    for key_node, value_node in zip(node.keys, node.values, strict=True):
        kind, key = ast.literal_eval(key_node)
        for prov_node, entry in zip(value_node.keys, value_node.values, strict=True):
            provenance = ast.literal_eval(prov_node)
            for driver in sorted(drivers(entry)):
                rows.append((kind, key, provenance, driver))
    return rows


# ---- the rule explains the recorded reality ----------------------------------


def test_the_rule_explains_every_recorded_divergence():
    """The whole claim. A rule that contradicted a shipped driver would be a
    rule about a system we do not have."""
    unexplained = [
        f"{kind}.{key} as {provenance} by {driver}"
        for kind, key, provenance, driver in _ledger()
        if provenance != "conditional" and violation(key, driver, provenance)
    ]

    assert not unexplained, "ledger rows the policy does not permit: " + "; ".join(unexplained)


def test_the_ledger_is_not_empty():
    """Guards the test above against passing because nothing was parsed."""
    assert len(_ledger()) >= 20


# ---- classification -----------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    ["DATABASE_PASSWORD", "REDIS_AUTH_TOKEN", "STORAGE_ACCESS_KEY", "AZURE_CONNECTION_STRING"],
)
def test_credential_keys_must_be_referenced(key):
    assert classify_key(key) is KeyClass.CREDENTIAL
    assert allowed_provenance(key, "any/driver/here") == frozenset({SECRET_REF})


@pytest.mark.parametrize("key", ["DATABASE_HOST", "DATABASE_PORT", "DATABASE_NAME", "BUCKET"])
def test_public_keys_must_be_literals(key):
    assert classify_key(key) is KeyClass.PUBLIC
    assert allowed_provenance(key, "aws/postgres/aurora_postgres") == frozenset({LITERAL})


@pytest.mark.parametrize("key", ["REDIS_URL", "DOCDB_URI", "DATABASE_URL", "KAFKA_DSN"])
def test_connection_strings_are_decided_per_provider(key):
    """REDIS_URL reads as a three-way divergence and is not: every driver
    references exactly when its URL embeds a credential. Azure always
    authenticates, an in-cluster operator never does."""
    assert classify_key(key) is KeyClass.AUTH_BEARING_URL
    assert allowed_provenance(key, "any/driver/here") == frozenset({LITERAL, SECRET_REF})


@pytest.mark.parametrize("key", ["KMS_KEY_NAME", "MASTER_SECRET_ARN", "SIGNING_KEY_ID"])
def test_pointers_to_secrets_are_not_themselves_secrets(key):
    """`*_SECRET_ARN` names where a secret lives. Treating it as credential-
    bearing would require a reference to a reference."""
    assert classify_key(key) is KeyClass.PUBLIC


# ---- the two mistakes the rule exists to catch --------------------------------


def test_a_credential_emitted_as_a_literal_is_refused():
    """It would land in ManagedServiceBinding.env_value_ref, a plaintext column."""
    message = violation("DATABASE_PASSWORD", "aws/postgres/aurora_postgres", LITERAL)

    assert message and "plaintext" in message


def test_a_public_value_emitted_as_a_reference_is_refused():
    """It invents a secret per value and makes an unresolvable reference a hard
    deploy failure for data already public inside the cluster."""
    message = violation("DATABASE_PORT", "azure/postgres/azure_pg_flex", SECRET_REF)

    assert message and "already public" in message


def test_the_refusal_says_which_exemption_to_reach_for():
    """The two exemptions mean different things, and picking the wrong one
    hides why a driver cannot inline the value."""
    message = violation("DATABASE_PORT", "azure/postgres/azure_pg_flex", SECRET_REF)

    assert "OPERATOR_GENERATED_DRIVERS" in message
    assert "PASS_THROUGH_REFERENCES" in message


# ---- exemptions ----------------------------------------------------------------


def test_an_operator_backed_driver_may_reference_a_public_key():
    """CNPG's controller generates the value into its own Secret and rotates
    it. The driver never learns it, so a literal would go stale."""
    assert violation("DATABASE_HOST", "k8s_native/postgres/cnpg", SECRET_REF) is None


def test_a_pass_through_reference_is_scoped_to_its_keys():
    """FSx Windows forwards the caller's mount identity untouched. That does
    not license it to reference the rest of its envelope."""
    assert violation("FILESYSTEM_USERNAME", "aws/filesystem/fsx_windows", SECRET_REF) is None
    assert violation("FILESYSTEM_MOUNT_PATH", "aws/filesystem/fsx_windows", SECRET_REF)


def test_an_exemption_never_licenses_inlining_a_credential():
    """The exemptions are about not knowing a value, not about sensitivity.
    Neither may put a credential in a plaintext column."""
    for driver in ("k8s_native/postgres/cnpg", "aws/filesystem/fsx_windows"):
        assert violation("DATABASE_PASSWORD", driver, LITERAL)


def test_the_exemption_lists_name_drivers_that_exist():
    """A stale entry silently widens the rule for a driver id nobody uses."""
    root = pathlib.Path(__file__).resolve().parents[1]
    listed = set(OPERATOR_GENERATED_DRIVERS) | set(PASS_THROUGH_REFERENCES)

    for driver_id in sorted(listed):
        cloud = driver_id.split("/", 1)[0]
        assert (root / cloud).is_dir(), f"{driver_id} names a cloud that does not exist"
