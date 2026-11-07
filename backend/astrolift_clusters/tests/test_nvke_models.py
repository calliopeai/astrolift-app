from __future__ import annotations

import datetime as dt
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from astrolift_clusters.models import NvkeConnection, NvkeOperation, NvkeSite
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_identity.models import Organization
from core.secrets import decrypt, encrypt_at_rest

pytestmark = pytest.mark.django_db


@pytest.fixture
def site():
    suffix = uuid4().hex[:12]
    org = Organization.objects.create(name="NVKE owner", slug=f"nvke-{suffix}")
    return NvkeSite.objects.create(
        organization=org, name="GPU site", slug="gpu-site", organization_namespace="org-owned"
    )


def connection(site, **kwargs):
    return NvkeConnection.objects.create(
        site=site,
        surface=kwargs.pop("surface", "controller"),
        endpoint="https://nvke.example.test/api",
        **kwargs,
    )


def operation(site, **kwargs):
    actor = kwargs.pop("actor", None) or get_user_model().objects.create(username="nvke-" + uuid4().hex[:12])
    encrypted = encrypt_at_rest(b'{"body":{"name":"cluster-a"}}')
    return NvkeOperation.objects.create(
        site=site,
        organization=kwargs.pop("organization", site.organization),
        actor=actor,
        request_id=kwargs.pop("request_id", uuid4()),
        request_digest="a" * 64,
        operation_key="createCluster",
        expected_site_version=site.version,
        input_backend_kind=encrypted.backend_kind,
        input_ciphertext=encrypted.backend_ref,
        deadline=timezone.now() + dt.timedelta(hours=1),
        **kwargs,
    )


def test_site_slug_is_owned_by_an_org_and_soft_deletion_keeps_history(site):
    with transaction.atomic(), pytest.raises(IntegrityError):
        NvkeSite.objects.create(
            organization=site.organization, name="Duplicate", slug=site.slug, organization_namespace="another"
        )
    other = Organization.objects.create(name="Other", slug="nvke-other-" + uuid4().hex[:8])
    NvkeSite.objects.create(
        organization=other, name="Other site", slug=site.slug, organization_namespace="other"
    )
    guid = site.guid
    site.soft_delete()
    assert not NvkeSite.objects.filter(guid=guid).exists()
    assert NvkeSite.all_objects.filter(guid=guid).exists()
    recreated = NvkeSite.objects.create(
        organization=site.organization,
        name="Replacement",
        slug=site.slug,
        organization_namespace="replacement",
    )
    assert recreated.guid != guid


@pytest.mark.parametrize("field,value", [("organization_namespace", "foreign-org"), ("organization_id", 0)])
def test_site_authority_cannot_be_reassigned(site, field, value):
    setattr(site, field, value)
    with pytest.raises(ValueError, match="ownership"):
        site.save()


def test_connection_surface_is_unique_and_cannot_be_repurposed(site):
    row = connection(site)
    with transaction.atomic(), pytest.raises(IntegrityError):
        connection(site)
    row.surface = "datastore"
    with pytest.raises(ValueError, match="ownership"):
        row.save()
    row.refresh_from_db()
    row.soft_delete()
    assert connection(site).guid != row.guid
    assert NvkeConnection.all_objects.filter(site=site, surface="controller").count() == 2


@pytest.mark.parametrize("kwargs", [{"surface": "proxy"}, {"timeout_seconds": 0}, {"timeout_seconds": 301}])
def test_connection_protocol_and_timeout_constraints_are_enforced_in_postgres(site, kwargs):
    with transaction.atomic(), pytest.raises(IntegrityError):
        connection(site, **kwargs)


def test_operation_org_cannot_differ_from_its_site(site):
    other = Organization.objects.create(name="Other", slug="nvke-foreign-" + uuid4().hex[:8])
    with pytest.raises(ValueError, match="site's organization"):
        operation(site, organization=other)


def test_request_id_cannot_be_reused_even_after_soft_deletion(site):
    first = operation(site)
    with transaction.atomic(), pytest.raises(IntegrityError):
        operation(site, actor=first.actor, request_id=first.request_id)
    first.soft_delete()
    with transaction.atomic(), pytest.raises(IntegrityError):
        operation(site, actor=first.actor, request_id=first.request_id)


@pytest.mark.parametrize(
    "field,value",
    [
        ("operation_key", "deleteCluster"),
        ("request_digest", "b" * 64),
        ("expected_site_version", 999),
        ("input_ciphertext", b"changed"),
    ],
)
def test_submitted_operation_identity_and_original_input_are_immutable(site, field, value):
    row = operation(site)
    setattr(row, field, value)
    with pytest.raises(ValueError, match="request identity"):
        row.save()


def test_original_input_roundtrips_through_the_real_secrets_backend(site):
    row = operation(site)
    assert b"cluster-a" not in bytes(row.input_ciphertext)
    row.refresh_from_db()
    from core.secrets import EncryptedSecret

    assert (
        decrypt(EncryptedSecret(row.input_backend_kind, bytes(row.input_ciphertext)))
        == b'{"body":{"name":"cluster-a"}}'
    )


@pytest.mark.parametrize("status", ["uncertain", "awaiting_ready", "reconciling", "running", "queued"])
def test_acceptance_or_ambiguity_does_not_mark_an_operation_complete(site, status):
    row = operation(site, status=status)
    assert not row.terminal
    row.cancellation_requested_at = timezone.now()
    row.save(update_fields=["cancellation_requested_at", "updated_at", "version"])
    assert not row.terminal


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled"])
def test_only_final_outcomes_are_terminal(site, status):
    assert operation(site, status=status).terminal
