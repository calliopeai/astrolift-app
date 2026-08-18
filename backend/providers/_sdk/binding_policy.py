"""When a binding value must be a secret reference rather than a literal (#1410).

Ten envelope keys arrive as a secrets-backend reference from one driver and an
inline literal from another. Downstream this is not a parse hazard: the app
resolves a reference before writing the bindings Secret, so the workload sees
the same flat string either way. It matters in three other places.

A credential emitted as a literal sits in ``ManagedServiceBinding.env_value_ref``,
a plaintext column. ``agent_secrets`` routes ``is_secret`` rows to a resolvable
URI and everything else to plain env vars. And an unresolvable reference is a
hard deploy failure, so marking a non-secret as one buys a new way to fail for
no benefit.

So the rule is about the *value*, not the driver:

* A key that carries a credential must be a reference. Inlining puts a live
  secret in a plaintext column.
* A key that carries no credential must be a literal. Referencing a hostname
  means inventing a secret per value, a resolve round trip, and a deploy-time
  failure mode for data that is already public inside the cluster.
* A URL-shaped key is either, decided per driver by whether that provider's URL
  embeds auth. ``REDIS_URL`` looks like a three-way divergence and is not: every
  driver emits a reference exactly when the URL carries a credential. Azure
  always authenticates, an in-cluster operator never does.

The exemptions are about knowledge rather than sensitivity: a driver cannot
inline a value it does not have. That happens two ways. An operator-backed
driver hands provisioning to a controller that generates credentials into its
own Secret and rotates them, so the driver never learns the value and a
snapshot would go stale. Or the operator supplied a reference in the first
place and the driver forwards it untouched, where dereferencing it only to
re-reference it would be pointless work with a new failure mode.

Checked against the recorded divergences in
``tests/test_binding_envelope_contract.py``: the rule explains all of them,
which is the evidence that it describes the drivers rather than replacing them.
"""

from __future__ import annotations

import re
from enum import StrEnum

LITERAL = "literal"
SECRET_REF = "secret_ref"


class KeyClass(StrEnum):
    CREDENTIAL = "credential"
    """Carries authentication material. Must be a reference."""

    PUBLIC = "public"
    """Carries no authentication material. Must be a literal."""

    AUTH_BEARING_URL = "auth_bearing_url"
    """A connection string that may or may not embed credentials, depending on
    how the provider authenticates. Either provenance is correct; which one is
    a property of the provider, not a decision the driver gets to make."""


#: Suffixes that make a key credential-bearing whatever precedes them. Matched
#: on the bare key, after the envelope prefix has been stripped.
_CREDENTIAL_SUFFIXES: tuple[str, ...] = (
    "_PASSWORD",
    "_PASSWD",
    "_SECRET",
    "_SECRET_KEY",
    "_TOKEN",
    "_API_KEY",
    "_ACCESS_KEY",
    "_PRIVATE_KEY",
    "_CREDENTIALS",
    "_SAS",
    "_SAS_TOKEN",
    "_CONNECTION_STRING",
)

#: Keys that are credential-bearing by name even without a matching suffix.
#:
#: Connection strings are deliberately absent. ``DOCDB_URI`` looks like it
#: belongs here and does not: Firestore authenticates with IAM and the Percona
#: MongoDB URI names a replica set, so neither embeds a credential. Whether a
#: URI carries auth is a property of the provider, so those classify as
#: :attr:`KeyClass.AUTH_BEARING_URL` and are decided per driver.
_CREDENTIAL_KEYS: frozenset[str] = frozenset({"PASSWORD", "SECRET"})

#: Connection strings whose credential content depends on the provider.
_URL_PATTERN = re.compile(r"(?:^|_)(URL|URI|DSN|ENDPOINT_URL)$")

#: Keys that look credential-shaped but are not. ``*_KEY_NAME`` names a key, it
#: is not one; ``*_SECRET_NAME`` and ``*_SECRET_ARN`` are pointers, which is the
#: whole point of a reference and not itself sensitive.
_NOT_CREDENTIAL: tuple[str, ...] = (
    "_KEY_NAME",
    "_KEY_ID",
    "_SECRET_NAME",
    "_SECRET_ARN",
    "_SECRET_ID",
    "_TOKEN_NAME",
    "_KEY_VAULT",
    "_KEY_ALIAS",
)

#: Drivers that hand provisioning to a controller which generates credentials
#: into its own Secret. They must reference even a key this rule calls public,
#: because they never learn the value and a snapshot would go stale on the
#: controller's next rotation.
OPERATOR_GENERATED_DRIVERS: frozenset[str] = frozenset(
    {
        "k8s_native/postgres/cnpg",
        "k8s_native/mysql/operator",
        "k8s_native/document_db/mongodb_operator",
        "k8s_native/redis/operator",
        "k8s_native/mssql/sqlserver_express",
        "k8s_native/event_stream/kafka_strimzi",
        "k8s_native/search/opensearch_operator",
    }
)


#: Keys a driver receives as a reference from the operator and passes through
#: untouched. The driver never sees the value, so it cannot inline it, and
#: dereferencing it just to re-reference it would be pointless work with a new
#: failure mode. Narrower than the operator-generated exemption: it names the
#: specific keys, because a driver that passes one value through still owns the
#: rest of its envelope.
PASS_THROUGH_REFERENCES: dict[str, frozenset[str]] = {
    "aws/filesystem/fsx_windows": frozenset({"FILESYSTEM_USERNAME", "FILESYSTEM_PASSWORD"}),
}


def classify_key(key: str) -> KeyClass:
    """Decide what a binding key carries, from its name alone.

    By name rather than by value because the guardrail runs against binding
    schemas, where no value exists yet, and because a rule that needed a live
    value could only ever be checked in production.
    """
    bare = key.upper()
    if any(bare.endswith(suffix) for suffix in _NOT_CREDENTIAL):
        return KeyClass.PUBLIC
    if bare in _CREDENTIAL_KEYS or any(bare.endswith(s) for s in _CREDENTIAL_SUFFIXES):
        return KeyClass.CREDENTIAL
    if _URL_PATTERN.search(bare):
        return KeyClass.AUTH_BEARING_URL
    return KeyClass.PUBLIC


def allowed_provenance(key: str, driver_id: str) -> frozenset[str]:
    """Which provenances this driver may legitimately use for this key."""
    key_class = classify_key(key)
    if key_class is KeyClass.CREDENTIAL:
        return frozenset({SECRET_REF})
    if key_class is KeyClass.AUTH_BEARING_URL:
        return frozenset({LITERAL, SECRET_REF})
    if _does_not_own_the_value(key, driver_id):
        return frozenset({LITERAL, SECRET_REF})
    return frozenset({LITERAL})


def _does_not_own_the_value(key: str, driver_id: str) -> bool:
    """Whether this driver is structurally unable to inline this key.

    Two ways that happens, and both are about knowledge rather than
    sensitivity. A controller may generate the value into its own Secret and
    rotate it, so the driver never learns it and a snapshot would go stale. Or
    the operator supplied it as a reference in the first place, and the driver
    is passing it through.
    """
    if driver_id in OPERATOR_GENERATED_DRIVERS:
        return True
    return key.upper() in PASS_THROUGH_REFERENCES.get(driver_id, frozenset())


def violation(key: str, driver_id: str, provenance: str) -> str | None:
    """Describe why this provenance is wrong for this key, or None if it is fine."""
    allowed = allowed_provenance(key, driver_id)
    if provenance in allowed:
        return None
    if provenance == LITERAL:
        return (
            f"{driver_id} emits {key} as a literal, but the key is credential-bearing. "
            f"A literal lands in ManagedServiceBinding.env_value_ref, a plaintext column."
        )
    return (
        f"{driver_id} emits {key} as a secret_ref, but the key carries no credential. "
        f"Referencing it invents a secret per value, adds a resolve round trip, and "
        f"makes an unresolvable reference a hard deploy failure for data that is "
        f"already public inside the cluster. If this driver cannot know the value, "
        f"say which way: OPERATOR_GENERATED_DRIVERS when a controller generates and "
        f"rotates it, PASS_THROUGH_REFERENCES when the operator supplied the "
        f"reference and the driver forwards it untouched."
    )
