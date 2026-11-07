from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model

from astrolift_clusters import nvke_sites as service
from astrolift_clusters.models import NvkeConnection
from astrolift_clusters.tests.test_heartbeat import _no_opensearch_profile_index  # noqa: F401
from astrolift_identity.models import Member, Organization, Team
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import bind_role

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    suffix = uuid4().hex[:12]
    org = Organization.objects.create(name="NVKE owner", slug="nvke-sites-" + suffix)
    user = get_user_model().objects.create(username="nvke-owner-" + suffix, is_active=True)
    member = Member.objects.create(user=user, scope_kind="ORG", scope_id=org.pk)
    bind_role(
        user,
        permissions=[Permission.NVKE_READ, Permission.NVKE_MANAGE, Permission.NVKE_ADMIN],
        kind="ORG",
        scope_id=org.pk,
        slug="nvke-admin-" + suffix,
    )
    return SimpleNamespace(org=org, user=user, member=member)


def context(world):
    return tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk))


def create():
    return service.create_site(name="GPU site", slug="gpu-site", organization_namespace="org-owned")


def put(site, **kwargs):
    return service.put_connection(
        site_id=site.guid,
        expected_site_version=kwargs.pop("expected_site_version", site.version),
        expected_connection_version=kwargs.pop("expected_connection_version", 0),
        surface=kwargs.pop("surface", "controller"),
        endpoint=kwargs.pop("endpoint", "https://nvke.example.test/api"),
        **kwargs,
    )


def test_credentials_are_encrypted_and_config_changes_advance_site_authority(world):
    with context(world):
        site = create()
        initial_version = site.version
        connection = put(site, credentials={"token": "private-nvke-token"})
        stored = NvkeConnection.objects.get(pk=connection.pk)
        assert b"private-nvke-token" not in bytes(stored.credential_ciphertext)
        assert service.credentials_for(stored) == {"token": "private-nvke-token"}
        site.refresh_from_db()
        assert site.version > initial_version
        assert connection.verified_at is None
        assert service.read_site(site.guid).guid == site.guid
        assert [s.guid for s in service.list_sites()] == [site.guid]


def test_preserve_rotate_and_clear_credentials_are_distinct(world):
    with context(world):
        site = create()
        row = put(site, credentials={"token": "old-token"})
        old_ciphertext = bytes(row.credential_ciphertext)
        site.refresh_from_db()
        row = put(site, expected_connection_version=row.version, credentials=None)
        assert bytes(row.credential_ciphertext) == old_ciphertext
        site.refresh_from_db()
        row = put(site, expected_connection_version=row.version, credentials={"token": "new-token"})
        assert service.credentials_for(row) == {"token": "new-token"}
        site.refresh_from_db()
        row = put(site, expected_connection_version=row.version, credentials={})
        assert service.credentials_for(row) == {}


def test_stale_configuration_does_not_rotate_a_connection(world):
    with context(world):
        site = create()
        row = put(site, credentials={"token": "current"})
        with pytest.raises(service.NvkeSiteError) as stale:
            put(site, expected_connection_version=row.version, credentials={"token": "stale-replacement"})
        assert stale.value.code == "VERSION_MISMATCH"
        row.refresh_from_db()
        assert service.credentials_for(row) == {"token": "current"}
        site.refresh_from_db()
        with pytest.raises(service.NvkeSiteError) as connection_stale:
            put(site, expected_connection_version=row.version - 1, credentials={"token": "stale-replacement"})
        assert connection_stale.value.code == "VERSION_MISMATCH"


@pytest.mark.parametrize(
    "credentials",
    [
        {"token": "token with spaces"},
        {"token": "private\r\nX-Injected: header"},
        {"unrecognized": "private-secret"},
        {"token": None},
        {"client_key_pem": "private-secret"},
        {"client_certificate_pem": "certificate-without-key"},
        {"token": "a" * 16385},
    ],
)
def test_invalid_credentials_do_not_enter_storage_or_error_messages(world, credentials):
    with context(world):
        site = create()
        with pytest.raises(service.NvkeSiteError) as exc:
            put(site, credentials=credentials)
        assert exc.value.code == "INVALID_INPUT"
        assert "private-secret" not in str(exc.value)
        assert not NvkeConnection.objects.filter(site=site).exists()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://nvke.example.test/api",
        "https://user:private-secret@nvke.example.test/api",
        "https://nvke.example.test/api?token=private-secret",
        "https://nvke.example.test/../api",
    ],
)
def test_invalid_endpoint_is_never_stored_or_echoed(world, endpoint):
    with context(world):
        site = create()
        with pytest.raises(service.NvkeSiteError) as exc:
            put(site, endpoint=endpoint)
        assert exc.value.code == "INVALID_INPUT"
        assert "private-secret" not in str(exc.value)
        assert not NvkeConnection.objects.filter(site=site).exists()


def test_foreign_and_missing_site_ids_have_the_same_outcome(world):
    other_org = Organization.objects.create(name="Foreign", slug="nvke-other-" + uuid4().hex[:10])
    from astrolift_clusters.models import NvkeSite

    other = NvkeSite.objects.create(
        organization=other_org, name="Foreign", slug="foreign", organization_namespace="foreign"
    )
    errors = []
    with context(world):
        for guid in (other.guid, uuid4(), "malformed"):
            with pytest.raises(service.NvkeSiteError) as exc:
                service.read_site(guid)
            errors.append((exc.value.code, str(exc.value)))
        assert list(service.list_sites()) == []
    assert len(set(errors)) == 1


def test_membership_revocation_removes_authority_even_with_surviving_role_grants(world):
    with context(world):
        site = create()
        world.member.soft_delete()
        with pytest.raises(PermissionDenied):
            service.read_site(site.guid)
        with pytest.raises(PermissionDenied):
            put(site, credentials={"token": "must-not-store"})
        assert not NvkeConnection.objects.filter(site=site).exists()


def test_team_grant_cannot_authorize_an_organization_owned_site(world):
    suffix = uuid4().hex[:10]
    team = Team.objects.create(organization=world.org, name="Team", slug="nvke-team-" + suffix)
    user = get_user_model().objects.create(username="nvke-team-user-" + suffix, is_active=True)
    Member.objects.create(user=user, scope_kind="ORG", scope_id=world.org.pk)
    bind_role(
        user, permissions=[Permission.NVKE_ADMIN], kind="TEAM", scope_id=team.pk, slug="team-admin-" + suffix
    )
    with tenant_context(TenantContext(organization_id=world.org.pk, team_id=team.pk, actor_user_id=user.pk)):
        with pytest.raises(PermissionDenied):
            create()


def test_site_removal_is_soft_and_does_not_return_usable_connections(world):
    with context(world):
        site = create()
        put(site, credentials={"token": "retained-encrypted-history"})
        site.refresh_from_db()
        service.remove_site(site_id=site.guid, expected_site_version=site.version)
        with pytest.raises(service.NvkeSiteError) as exc:
            service.read_site(site.guid)
        assert exc.value.code == "NOT_FOUND"
        assert list(service.list_sites()) == []


def test_explicit_disabling_bumps_authority_without_deleting_the_site(world):
    with context(world):
        site = create()
        disabled = service.set_site_enabled(
            site_id=site.guid, expected_site_version=site.version, enabled=False
        )
        assert not disabled.is_active
        assert disabled.version > site.version
        assert service.read_site(site.guid).is_active is False


def test_uncertain_operations_prevent_site_removal_until_the_outcome_is_resolved(world):
    from astrolift_clusters.tests.test_nvke_models import operation

    with context(world):
        site = create()
        pending = operation(site, actor=world.user, status="uncertain")
        with pytest.raises(service.NvkeSiteError) as exc:
            service.remove_site(site_id=site.guid, expected_site_version=site.version)
        assert exc.value.code == "PRECONDITION"
        assert service.read_site(site.guid).guid == site.guid
        pending.status = "failed"
        pending.save(update_fields=["status", "updated_at", "version"])
        service.remove_site(site_id=site.guid, expected_site_version=site.version)
