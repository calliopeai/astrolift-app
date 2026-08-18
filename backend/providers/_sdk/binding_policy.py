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
#: A key is credential-bearing when its LAST segment is one of these. Segment
#: rather than substring, and last rather than anywhere, because both looser
#: forms were tried and both were wrong: substring matching classified
#: ``WIDE_COLUMN_KEYSPACE`` and ``PRIVATE_ENDPOINT_ID`` as credentials, and
#: anywhere-matching classified every ``ENCRYPTION_KEY_*`` attribute that way.
#: These mean the value IS one, wherever they appear. ``PASSWORD`` in
#: ``FILESYSTEM_PASSWORD_SECONDARY`` is not describing something, it is the
#: thing.
_CREDENTIAL_ANYWHERE: frozenset[str] = frozenset({"PASSWORD", "PASSWD", "SECRET", "TOKEN", "CREDENTIALS", "SAS"})

#: These only mean it at the end. ``KEY`` and ``CERT`` are ordinary nouns in
#: attribute names: ``ENCRYPTION_KEY_MULTI_REGION`` is a boolean about a key,
#: ``GCP_KMS_KEY_RING`` is a container for keys, ``ENCRYPTION_KEY_SPEC``
#: describes one. Matching them anywhere classified all three as secrets.
_CREDENTIAL_TAIL_SEGMENTS: frozenset[str] = frozenset({"KEY", "CERT", "CERTIFICATE"})

#: Multi-word tails that mean the value embeds credentials. Separate because
#: they span segments, so neither single-segment rule above sees them.
_CREDENTIAL_TAIL_PHRASES: tuple[str, ...] = ("_CONNECTION_STRING", "_SAS_TOKEN", "_SAS_URL")

#: Tails that turn a credential-shaped name into a pointer or an attribute.
#: ``*_SECRET_REF`` and ``*_KEY_ARN`` say where a secret lives, which is the
#: entire point of a reference and not itself sensitive; ``*_KEY_SPEC`` and
#: ``*_KEY_USAGE`` describe a key rather than containing one.
_POINTER_TAILS: frozenset[str] = frozenset(
    {"REF", "ARN", "NAME", "NAMES", "ID", "IDS", "RING", "SPEC", "USAGE", "ALIAS", "VAULT"}
)

#: Certificate names that are trust anchors rather than credentials. A CA
#: certificate is published on purpose.
_PUBLIC_CERTS: frozenset[str] = frozenset(
    {
        "CA_CERT",
        "CA_CERTIFICATE",
        "SERVER_CERT",
        # A boolean switch about whether to verify, not a certificate.
        "TRUST_SERVER_CERTIFICATE",
    }
)

#: Keys that are credential-bearing by name even without a matching suffix.
#:
#: Connection strings are deliberately absent. ``DOCDB_URI`` looks like it
#: belongs here and does not: Firestore authenticates with IAM and the Percona
#: MongoDB URI names a replica set, so neither embeds a credential. Whether a
#: URI carries auth is a property of the provider, so those classify as
#: :attr:`KeyClass.AUTH_BEARING_URL` and are decided per driver.
_CREDENTIAL_KEYS: frozenset[str] = frozenset(
    {
        "PASSWORD",
        "SECRET",
        # Semi-public in isolation, and every provider treats it as one half of
        # a credential pair. Referencing it is what the drivers already do.
        "AWS_ACCESS_KEY_ID",
    }
)

#: Identity keys that are a credential for some providers and a plain
#: identifier for others. The ledger already recorded both directions for
#: ``DOCDB_USER`` and ``MYSQL_USER``, with a provider reason each way: a
#: username the driver chose itself is knowable, one an operator generates
#: alongside a password is not. Treated like connection strings.
_AMBIGUOUS_IDENTITY_MARKERS: tuple[str, ...] = ("USER", "USERNAME", "PRINCIPAL", "IDENTITY")

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
        # cert-manager mints and rotates the tenant Temporal client certificate
        # through the operator; the driver only ever sees the Secret's name.
        "k8s_native/workflow_engine/temporal",
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
    if bare in _CREDENTIAL_KEYS:
        return KeyClass.CREDENTIAL
    segments = bare.split("_")
    if any(bare.endswith(tail) for tail in _PUBLIC_CERTS):
        return KeyClass.PUBLIC
    if segments[-1] in _POINTER_TAILS:
        return KeyClass.PUBLIC
    if any(bare.endswith(phrase) for phrase in _CREDENTIAL_TAIL_PHRASES):
        return KeyClass.CREDENTIAL
    if any(segment in _CREDENTIAL_ANYWHERE for segment in segments):
        return KeyClass.CREDENTIAL
    if segments[-1] in _CREDENTIAL_TAIL_SEGMENTS:
        return KeyClass.CREDENTIAL
    if _URL_PATTERN.search(bare) or any(m in segments for m in _AMBIGUOUS_IDENTITY_MARKERS):
        return KeyClass.AUTH_BEARING_URL
    return KeyClass.PUBLIC


def allowed_provenance(key: str, driver_id: str) -> frozenset[str]:
    """Which provenances this driver may legitimately use for this key.

    Advisory, and wider than it looks: only a credential is actually
    constrained. Everything else may be referenced, because a driver that
    cannot know a value must reference it and a key's name cannot tell the two
    apart. See :func:`violation` for why that asymmetry is deliberate.
    """
    if classify_key(key) is KeyClass.CREDENTIAL:
        return frozenset({SECRET_REF})
    return frozenset({LITERAL, SECRET_REF})


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
    """Describe why this provenance is unsafe, or None.

    Only one direction is enforced, and that asymmetry is the finding rather
    than a shortcut.

    A credential emitted as a literal lands in a plaintext column. That is a
    leak, it is irreversible once written, and running this rule across every
    binding in the tree found **zero** instances of it. So it costs nothing to
    forbid and is worth forbidding permanently.

    A public value emitted as a reference is merely wasteful: an invented
    secret, a resolve round trip, and a deploy that can fail on a missing
    reference. It cannot leak anything. Enforcing that direction was tried and
    produced 19 false positives on the first pass and 46 on the second, every
    one of them a driver being more careful than the rule. Two independent
    calibrations both failing that way is evidence that a key's name does not
    reliably say a value is *not* sensitive, only that it is. The ledger in
    ``test_binding_envelope_contract`` still records those cases with reasons,
    which is the right instrument for a style question.
    """
    if provenance == LITERAL and classify_key(key) is KeyClass.CREDENTIAL:
        return (
            f"{driver_id} emits {key} as a literal, but the key is credential-bearing. "
            f"A literal is stored in ManagedServiceBinding.env_value_ref, a plaintext "
            f"column, and cannot be unwritten. Emit it as a secret_ref."
        )
    return None
