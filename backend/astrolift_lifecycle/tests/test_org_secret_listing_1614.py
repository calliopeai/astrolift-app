"""
``list_org_secrets`` (#1614).

``astrolift_pipelines.pipeline_secrets.get_pipeline_secret_names`` has
imported this function since #100. It was never written, and the import
sits inside a ``try: ... except Exception: return []`` -- so the secret
names a pipeline shows have always been an empty list, presented as
"this pipeline has no secrets" rather than as a failure.
"""

from __future__ import annotations

import pytest

from astrolift_identity.models import Organization
from astrolift_lifecycle.services.secrets import (
    delete_org_secret,
    list_org_secrets,
    write_org_secret,
)

pytestmark = pytest.mark.django_db

PREFIX = "astrolift/pipelines/abc/secrets/"


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme-1614")


def test_listing_returns_the_keys_written(org):
    write_org_secret(org, PREFIX + "DATABASE_URL", "postgres://x")
    write_org_secret(org, PREFIX + "API_KEY", "k")

    assert list_org_secrets(org) == [PREFIX + "API_KEY", PREFIX + "DATABASE_URL"]


def test_the_prefix_narrows_to_one_pipeline(org):
    write_org_secret(org, PREFIX + "API_KEY", "k")
    write_org_secret(org, "astrolift/pipelines/other/secrets/API_KEY", "k")

    assert list_org_secrets(org, prefix=PREFIX) == [PREFIX + "API_KEY"]


def test_listing_is_scoped_to_the_organization(org):
    other = Organization.objects.create(name="Other", slug="other-1614")
    write_org_secret(org, PREFIX + "MINE", "m")
    write_org_secret(other, PREFIX + "THEIRS", "t")

    assert list_org_secrets(org) == [PREFIX + "MINE"]
    assert list_org_secrets(other) == [PREFIX + "THEIRS"]


def test_a_deleted_secret_leaves_the_listing(org):
    """Soft delete is still delete to a reader. A key that stayed listed
    after deletion would tell an operator a secret is set when reading it
    returns nothing."""
    write_org_secret(org, PREFIX + "GONE", "g")
    delete_org_secret(org, PREFIX + "GONE")

    assert list_org_secrets(org) == []


def test_an_org_with_no_secrets_lists_nothing(org):
    assert list_org_secrets(org) == []


def test_values_are_not_returned(org):
    """Names only. The listing must never become a bulk decrypt."""
    write_org_secret(org, PREFIX + "API_KEY", "super-secret-value")

    assert "super-secret-value" not in "".join(list_org_secrets(org))
