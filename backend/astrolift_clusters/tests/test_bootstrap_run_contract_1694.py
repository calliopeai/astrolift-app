"""The bootstrap-run input is a cross-repo contract, pinned here (#1694).

`astro cluster bootstrap` records its outcome through
`recordClusterBootstrapRun`. Every field it sent was once wrong --
`clusterId` / `releases` / `success` / `cloud` against this input, which
wants `clusterSlug` / `status` / `installedReleases` / `cliVersion` /
`hostInfo`. Not one name lined up:

    Field 'clusterSlug' of required type 'String!' was not provided.
    Field 'clusterId' is not defined by type 'RecordClusterBootstrapRunInput'.

The write is warn-only, so bootstrap still succeeded and nobody was
blocked -- which is exactly why it went unnoticed: **no bootstrap run had
ever been recorded on any install**, and the cluster status tab had
nothing to show. The completeness of the mismatch is the tell that the
two sides were never exercised together.

The same field set is pinned on the CLI side in
`cmd/cluster_bootstrap_contract_test.go` (astrolift-cli). Renaming a
field on either side without the other fails one of the two tests. That
is the whole point: neither repo can see the other at test time, so the
shared literal is the seam.
"""

from __future__ import annotations

import dataclasses

import strawberry

from astrolift_clusters.schema.mutations import RecordClusterBootstrapRunInput

# Keep sorted, and keep identical to wantBootstrapRunFields in the CLI.
EXPECTED_FIELDS = [
    "chartVersion",
    "cliVersion",
    "clusterSlug",
    "endedAt",
    "errorMessage",
    "hostInfo",
    "installedReleases",
    "startedAt",
    "status",
]

# Fields the CLI must send on every call. `errorMessage` is the only
# optional one -- a successful run omits it rather than sending an empty
# string that would read as "there was an error and it had no message".
EXPECTED_REQUIRED = [
    "chartVersion",
    "cliVersion",
    "clusterSlug",
    "endedAt",
    "hostInfo",
    "installedReleases",
    "startedAt",
    "status",
]


def _graphql_fields() -> dict[str, object]:
    definition = RecordClusterBootstrapRunInput.__strawberry_definition__
    return {strawberry.utils.str_converters.to_camel_case(f.name): f for f in definition.fields}


def test_the_input_carries_exactly_the_fields_the_cli_sends():
    assert sorted(_graphql_fields()) == EXPECTED_FIELDS


def test_only_the_error_message_is_optional():
    """A field that becomes required without the CLI learning to send it
    breaks every bootstrap's record -- silently, because the write is
    warn-only."""

    fields = _graphql_fields()
    optional = {name for name, f in fields.items() if f.default is not dataclasses.MISSING}
    assert optional == {"errorMessage"}
    assert sorted(set(EXPECTED_FIELDS) - optional) == EXPECTED_REQUIRED


def test_status_is_a_string_not_a_boolean():
    """The CLI used to send a boolean under the name `success`. The
    contract is a post-run summary string (`succeeded` / `failed`), which
    leaves room for outcomes a bool cannot carry."""

    field = _graphql_fields()["status"]
    assert field.type is str
