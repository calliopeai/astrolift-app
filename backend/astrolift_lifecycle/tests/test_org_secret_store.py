"""The org secret store behind pipeline secrets (#1580 decision 2).

Real Postgres only - no DB mocks (workspace rule).

``astrolift_lifecycle.services.secrets`` is the module three call sites have
imported since #100 without it existing. Every import sits inside an exception
handler, so the failure was invisible: writes raised a generic RuntimeError,
and ``_read_org_secret`` returned None for every name, which made
``resolve_pipeline_secrets`` report every declared secret as missing.

These tests pin the contract those callers assume, and
``test_the_pipeline_secret_round_trip_works`` is the end-to-end proof that the
subsystem the module was written for now functions.
"""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization
from astrolift_lifecycle.models import OrgSecret
from astrolift_lifecycle.services.secrets import (
    delete_org_secret,
    read_org_secret,
    write_org_secret,
)

pytestmark = pytest.mark.django_db

KEY = "astrolift/pipelines/abc/secrets/TOKEN"


@pytest.fixture
def org():
    return Organization.objects.create(name="acme")


def test_a_value_round_trips(org):
    write_org_secret(org, KEY, "s3cret")

    assert read_org_secret(org, KEY) == "s3cret"


def test_an_unset_key_reads_as_none(org):
    assert read_org_secret(org, KEY) is None


def test_a_write_is_an_upsert(org):
    write_org_secret(org, KEY, "first")
    write_org_secret(org, KEY, "second")

    assert read_org_secret(org, KEY) == "second"
    assert OrgSecret.objects.filter(organization=org, key=KEY).count() == 1


def test_the_plaintext_is_not_stored(org):
    """The whole point of the envelope. A row that holds the value in the clear
    would pass every other test here."""
    write_org_secret(org, KEY, "s3cret")

    row = OrgSecret.objects.get(organization=org, key=KEY)
    assert b"s3cret" not in bytes(row.ciphertext)
    assert row.backend_kind


def test_one_orgs_secret_is_invisible_to_another(org):
    other = Organization.objects.create(name="other")
    write_org_secret(org, KEY, "s3cret")

    assert read_org_secret(other, KEY) is None


def test_a_deleted_secret_reads_as_unset(org):
    write_org_secret(org, KEY, "s3cret")
    delete_org_secret(org, KEY)

    assert read_org_secret(org, KEY) is None


def test_deleting_an_absent_key_is_a_noop(org):
    delete_org_secret(org, KEY)  # must not raise

    assert read_org_secret(org, KEY) is None


def test_a_key_can_be_rewritten_after_deletion(org):
    """The unique constraint is scoped to live rows, so a soft-deleted key must
    not block reuse of the same name."""
    write_org_secret(org, KEY, "first")
    delete_org_secret(org, KEY)
    write_org_secret(org, KEY, "second")

    assert read_org_secret(org, KEY) == "second"


def test_an_undecryptable_row_reads_as_unset(org):
    """Not distinguished from "not set" on purpose: unusable at spawn either
    way, and reporting the difference tells a caller a secret exists without
    letting them read it."""
    write_org_secret(org, KEY, "s3cret")
    OrgSecret.objects.filter(organization=org, key=KEY).update(ciphertext=b"garbage")

    assert read_org_secret(org, KEY) is None


def test_non_ascii_values_survive_the_round_trip(org):
    write_org_secret(org, KEY, "pa55w0rd-éè-\U0001f511")

    assert read_org_secret(org, KEY) == "pa55w0rd-éè-\U0001f511"


# ---------------------------------------------------------------------------
# the subsystem this unblocks
# ---------------------------------------------------------------------------


def test_the_pipeline_secret_round_trip_works(org):
    """End-to-end through the callers that had been importing a phantom module.

    Before this landed, ``set_pipeline_secret`` raised RuntimeError and
    ``_read_org_secret`` returned None for everything.
    """
    from astrolift_pipelines.pipeline_secrets import _secret_key, set_pipeline_secret
    from astrolift_pipelines.secret_plumbing import _read_org_secret

    class _Pipeline:
        guid = "11111111-1111-1111-1111-111111111111"
        name = "build"
        organization = org

    pipeline = _Pipeline()
    set_pipeline_secret(pipeline, "REGISTRY_TOKEN", "hunter2")

    assert _read_org_secret(org, _secret_key(pipeline, "REGISTRY_TOKEN")) == "hunter2"


def test_deleting_a_pipeline_secret_makes_it_unresolvable(org):
    from astrolift_pipelines.pipeline_secrets import (
        _secret_key,
        delete_pipeline_secret,
        set_pipeline_secret,
    )
    from astrolift_pipelines.secret_plumbing import _read_org_secret

    class _Pipeline:
        guid = "22222222-2222-2222-2222-222222222222"
        name = "build"
        organization = org

    pipeline = _Pipeline()
    set_pipeline_secret(pipeline, "REGISTRY_TOKEN", "hunter2")
    delete_pipeline_secret(pipeline, "REGISTRY_TOKEN")

    assert _read_org_secret(org, _secret_key(pipeline, "REGISTRY_TOKEN")) is None


def test_deletion_is_soft(org):
    """Repo law: soft delete on every business model, never hard delete. The
    rewrite-after-delete test above passes under a hard delete too, so without
    this the rule is unguarded."""
    write_org_secret(org, KEY, "s3cret")
    delete_org_secret(org, KEY)

    row = OrgSecret.all_objects.get(organization=org, key=KEY)
    assert row.deleted_at is not None
