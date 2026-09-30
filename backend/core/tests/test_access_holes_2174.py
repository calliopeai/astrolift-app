"""Upload writes and install diagnostics enforce their own authority (#2174)."""

from contextlib import contextmanager
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import Client, RequestFactory
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone

from astrolift_identity.api_tokens import (
    mint_token,
    reset_current_api_token,
    set_current_api_token,
)
from astrolift_identity.models import ApiToken, Member
from astrolift_identity.models import Organization as IdentityOrganization
from config.schema import schema
from core.models.upload import Upload
from core.schema.common import GlobalIDUtils
from core.schema.context import StrawberryContext

pytestmark = pytest.mark.django_db
User = get_user_model()
CONFIRM = """
mutation Confirm($url: String, $id: ID, $metadata: JSON, $delete: Boolean, $expiration: Date) {
  confirmPreSignedUrlImageUpload(
    publicUrl: $url, uploadId: $id, metadata: $metadata, delete: $delete, expirationDate: $expiration
  ) { ack }
}
"""


@pytest.fixture(autouse=True)
def no_search_side_effects(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, guid: None))


def _upload(tenant):
    return Upload.objects.create(
        organization=tenant.organization,
        created_by=tenant.user,
        public_url=f"https://uploads.example.test/{uuid4()}",
        pre_signed_url="https://uploads.example.test/put",
        content_type="image/png",
        metadata={"original": True},
    )


def _confirm(actor, upload, lookup, *, delete=False, public_url=None, expiration=None):
    request = RequestFactory().post("/app/gql/config/")
    request.user = actor
    request.session = {}
    variables = {"metadata": {"changed": True}, "delete": delete}
    if expiration:
        variables["expiration"] = expiration
    if lookup in {"id", "both"}:
        variables["id"] = GlobalIDUtils.to_global_id("UploadType", upload.pk)
    if lookup in {"url", "both"}:
        variables["url"] = public_url or upload.public_url
    return schema.execute_sync(CONFIRM, variable_values=variables, context_value=StrawberryContext(request))


def _unchanged(upload):
    upload.refresh_from_db()
    assert upload.metadata == {"original": True}
    assert upload.deleted_at is None
    assert upload.deleted_by_id is None
    assert upload.updated_by_id is None


@pytest.mark.parametrize("lookup", ["url", "id", "both"])
@pytest.mark.parametrize("delete", [False, True])
def test_own_upload_confirmation_and_soft_delete(two_tenants, lookup, delete):
    tenants = two_tenants
    upload = _upload(tenants.a)
    result = _confirm(tenants.a.user, upload, lookup, delete=delete)
    assert result.errors is None
    assert result.data["confirmPreSignedUrlImageUpload"]["ack"] is True
    upload.refresh_from_db()
    assert upload.metadata == {"changed": True}
    assert upload.updated_by_id == tenants.a.user.pk
    assert bool(upload.deleted_at) is delete
    if delete:
        assert upload.deleted_by_id == tenants.a.user.pk


@pytest.mark.parametrize("lookup", ["url", "id", "both"])
@pytest.mark.parametrize("privilege", ["member", "staff", "operator"])
@pytest.mark.parametrize("delete", [False, True])
def test_cross_organization_confirmation_never_writes(two_tenants, lookup, privilege, delete):
    tenants = two_tenants
    actor = tenants.a.user
    actor.is_staff = privilege != "member"
    actor.is_superuser = privilege == "operator"
    actor.save()
    upload = _upload(tenants.b)
    result = _confirm(actor, upload, lookup, delete=delete, expiration="2030-01-01")
    assert result.errors
    assert result.errors[0].message == "Upload not found."
    _unchanged(upload)


@pytest.mark.parametrize("lookup", ["url", "id"])
@pytest.mark.parametrize("privilege", ["member", "staff", "operator"])
def test_same_org_other_owner_needs_platform_operator(two_tenants, lookup, privilege):
    tenants = two_tenants
    actor = tenants.a.user
    actor.is_staff = privilege != "member"
    actor.is_superuser = privilege == "operator"
    actor.save()
    upload = _upload(tenants.a)
    upload.created_by = tenants.b.user
    upload.save()
    result = _confirm(actor, upload, lookup, delete=True)
    if privilege == "operator":
        assert result.errors is None
        upload.refresh_from_db()
        assert upload.deleted_by_id == actor.pk
    else:
        assert result.errors
        _unchanged(upload)


@pytest.mark.parametrize("lookup", ["url", "id"])
@pytest.mark.parametrize(
    "state", ["anonymous", "inactive", "membership-inactive", "membership-deleted", "org-deleted", "no-org"]
)
def test_confirmation_fails_closed_when_identity_or_membership_is_inactive(two_tenants, lookup, state):
    tenants = two_tenants
    upload = _upload(tenants.a)
    actor = tenants.a.user
    if state == "anonymous":
        actor = AnonymousUser()
    elif state == "inactive":
        actor.is_active = False
        actor.save()
    elif state.startswith("membership"):
        membership = tenants.a.membership
        if state == "membership-inactive":
            membership.is_active = False
        else:
            membership.deleted_at = timezone.now()
        membership.save()
    elif state == "org-deleted":
        tenants.a.organization.deleted_at = timezone.now()
        tenants.a.organization.save()
    else:
        actor = User.objects.create_user(username=f"no-org-{uuid4()}")
    assert _confirm(actor, upload, lookup, delete=True).errors
    _unchanged(upload)


@pytest.mark.parametrize("lookup", ["url", "id"])
def test_deleted_upload_is_not_confirmable(two_tenants, lookup):
    tenants = two_tenants
    upload = _upload(tenants.a)
    upload.deleted_at = timezone.now()
    upload.deleted_by = tenants.a.user
    upload.save()
    assert _confirm(tenants.a.user, upload, lookup).errors
    upload.refresh_from_db()
    assert upload.metadata == {"original": True}


def test_id_and_url_must_address_the_same_upload(two_tenants):
    tenants = two_tenants
    upload = _upload(tenants.a)
    other = _upload(tenants.a)
    assert _confirm(tenants.a.user, upload, "both", public_url=other.public_url).errors
    _unchanged(upload)
    _unchanged(other)


@contextmanager
def _token(actor, organization, scopes):
    Member.objects.get_or_create(user=actor, scope_kind="ORG", scope_id=organization.pk)
    minted = mint_token()
    token = ApiToken.objects.create(
        user=actor,
        organization=organization,
        name="access-2174",
        scopes=scopes,
        token_hash=minted.token_hash,
        token_last_4=minted.last4,
    )
    marker = set_current_api_token(token)
    try:
        yield minted.plaintext
    finally:
        reset_current_api_token(marker)


@pytest.mark.parametrize("lookup", ["url", "id"])
@pytest.mark.parametrize("scopes", [[], ["read:apps"], ["write:apps"], ["admin"]])
def test_upload_bearer_requires_admin_and_matching_org_identity(two_tenants, lookup, scopes):
    tenants = two_tenants
    upload = _upload(tenants.a)
    organization = IdentityOrganization.objects.create(
        name="Upload identity", guid=tenants.a.organization.guid
    )
    with _token(tenants.a.user, organization, scopes):
        result = _confirm(tenants.a.user, upload, lookup)
    if scopes == ["admin"]:
        assert result.errors is None
    else:
        assert result.errors
        _unchanged(upload)


def test_bearer_org_integer_collision_cannot_authorize_legacy_upload(two_tenants):
    tenants = two_tenants
    upload = _upload(tenants.a)
    organization = IdentityOrganization.objects.create(pk=tenants.a.organization.pk, name="Separate identity")
    with _token(tenants.a.user, organization, ["admin"]):
        assert _confirm(tenants.a.user, upload, "url", delete=True).errors
    _unchanged(upload)


@pytest.fixture
def upload_storage(monkeypatch):
    from storages.backends.s3 import S3Storage

    storage = S3Storage(
        access_key="test-access",
        secret_key="test-secret",
        bucket_name="test-uploads",
        endpoint_url="http://127.0.0.1:9000",
        custom_domain="uploads.example.test",
        region_name="us-east-1",
    )
    monkeypatch.setattr(Upload, "_media_store", storage, raising=False)


def _initiate(actor, upload_uuid, metadata):
    request = RequestFactory().post("/app/gql/config/")
    request.user = actor
    request.session = {}
    return schema.execute_sync(
        """mutation Start($target: ID!, $uuid: UUID!, $metadata: JSON) {
          preSignedUrlImageUpload(globalId: $target, uuid: $uuid, mimetype: "image/png", metadata: $metadata) {
            uuid publicUrl preSignedUrl
          }
        }""",
        variable_values={
            "target": GlobalIDUtils.to_global_id("ProfileType", actor.profile.pk),
            "uuid": str(upload_uuid),
            "metadata": metadata,
        },
        context_value=StrawberryContext(request),
    )


def test_upload_initiation_retries_same_owner_uuid(two_tenants, upload_storage):
    actor = two_tenants.a.user
    upload_uuid = uuid4()
    first = _initiate(actor, upload_uuid, {"attempt": 1})
    assert first.errors is None
    second = _initiate(actor, upload_uuid, {"attempt": 2})
    assert second.errors is None
    assert first.data["preSignedUrlImageUpload"]["uuid"] == second.data["preSignedUrlImageUpload"]["uuid"]
    assert Upload.objects.filter(pk=upload_uuid).count() == 1
    upload = Upload.objects.get(pk=upload_uuid)
    assert upload.metadata == {"attempt": 2}
    assert upload.created_by_id == actor.pk
    assert upload.organization_id == two_tenants.a.organization.pk


@pytest.mark.parametrize("scopes", [[], ["read:apps"], ["write:apps"], ["admin"]])
def test_upload_initiation_obeys_generic_bearer_ceiling(two_tenants, upload_storage, scopes):
    actor = two_tenants.a.user
    organization = IdentityOrganization.objects.create(
        name="Upload identity", guid=two_tenants.a.organization.guid
    )
    upload_uuid = uuid4()
    with _token(actor, organization, scopes):
        result = _initiate(actor, upload_uuid, {"attempt": 1})
    if scopes == ["admin"]:
        assert result.errors is None
        assert Upload.objects.filter(pk=upload_uuid, created_by=actor).exists()
    else:
        assert result.errors
        assert not Upload.objects.filter(pk=upload_uuid).exists()


@pytest.mark.parametrize("victim", ["other-org", "other-owner", "deleted"])
@pytest.mark.parametrize("operator", [False, True])
def test_supplied_uuid_cannot_reassign_or_revive_another_upload(
    two_tenants, upload_storage, victim, operator
):
    actor = two_tenants.a.user
    actor.is_superuser = operator
    actor.save()
    upload = _upload(two_tenants.b if victim == "other-org" else two_tenants.a)
    if victim == "other-owner":
        upload.created_by = two_tenants.b.user
    elif victim == "deleted":
        upload.deleted_at = timezone.now()
    upload.save()
    before = Upload.objects.filter(pk=upload.pk).values().get()
    result = _initiate(actor, upload.pk, {"hijacked": True})
    assert result.errors
    assert Upload.objects.filter(pk=upload.pk).values().get() == before


@pytest.mark.parametrize("privilege", ["anonymous", "member", "staff", "inactive-operator", "operator"])
def test_metrics_http_route_requires_active_platform_operator(two_tenants, privilege):
    tenants = two_tenants
    client = Client()
    if privilege != "anonymous":
        actor = tenants.a.user
        actor.is_staff = "operator" in privilege or privilege == "staff"
        actor.is_superuser = "operator" in privilege
        actor.is_active = privilege != "inactive-operator"
        actor.save()
        client.force_login(actor)
    response = client.get(reverse("metrics"))
    if privilege == "operator":
        assert response.status_code == 200
        assert "text/plain" in response["Content-Type"]
        assert b"# HELP" in response.content
    else:
        assert response.status_code == 403
        assert b"# HELP" not in response.content


@pytest.mark.parametrize("scopes", [["read:apps"], ["write:apps"], ["admin"]])
def test_metrics_bearer_operator_needs_admin_scope(two_tenants, scopes):
    tenants = two_tenants
    operator = tenants.a.user
    operator.is_superuser = True
    operator.is_staff = True
    operator.save()
    organization = IdentityOrganization.objects.create(name="Metrics identity")
    with _token(operator, organization, scopes) as bearer:
        response = Client().get(reverse("metrics"), HTTP_AUTHORIZATION=f"Bearer {bearer}")
    assert response.status_code == (200 if scopes == ["admin"] else 403)


@pytest.mark.parametrize("route", ["/app/sentry-debug/", "/app/test/open_telemetry/"])
def test_debug_routes_are_removed(route):
    with pytest.raises(Resolver404):
        resolve(route)
    assert Client().get(route).status_code == 404
