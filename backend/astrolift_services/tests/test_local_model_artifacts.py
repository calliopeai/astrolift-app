"""Real PostgreSQL authority/import plus actual S3 HTTPS delivery contracts."""

import hashlib
import json
import urllib.request
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
import strawberry
from django.contrib.auth import get_user_model

from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Member, Organization, Role, RoleBinding
from astrolift_services import local_model_artifacts as service
from astrolift_services.models.local_model_artifact import LocalModelArtifact
from astrolift_services.schema.local_model_artifacts import ModelArtifactsMutation, ModelArtifactsQuery
from core.permissions import PermissionDenied
from core.tenancy import TenantContext, tenant_context
from providers._sdk.local_model_artifact import ArtifactStoreUnavailable
from providers.tests._sdk.test_local_model_artifact_http import contents
from providers.tests._sdk.test_local_model_artifact_http import model_s3_wire as model_s3_wire

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda cls, p: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, p: None))
    user = get_user_model().objects.create(
        username="local-model-owner", email="local-model-owner@fixture.invalid"
    )
    org = Organization.objects.create(name="Fixture", slug="local-model-fixture")
    member = Member.objects.create(user=user, scope_kind="ORG", scope_id=org.pk, is_active=True)
    role = Role.objects.create(
        name="Source admin", slug="local-model-admin", scope_level="ORG", permissions=["org.update"]
    )
    RoleBinding.objects.create(user=user, role=role, scope_kind="ORG", scope_id=org.pk)
    token = ApiToken.objects.create(
        user=user, organization=org, name="Fixture", token_hash=uuid4().hex, scopes=["admin"]
    )
    return SimpleNamespace(user=user, org=org, member=member, token=token)


@contextmanager
def caller(owner):
    with tenant_context(TenantContext(organization_id=owner.org.pk, actor_user_id=owner.user.pk)):
        marker = set_current_api_token(owner.token)
        try:
            yield
        finally:
            reset_current_api_token(marker)


def files():
    return [
        {"name": name, "size_bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        for name, body in contents().items()
    ]


@pytest.fixture
def configured(settings, monkeypatch, request):
    wire = request.getfixturevalue("model_s3_wire")
    settings.AWS_STORAGE_BUCKET_NAME = "owned-models"
    settings.AWS_S3_REGION_NAME = "us-east-1"
    settings.AWS_S3_ENDPOINT_URL = wire["url"]
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "owned-fixture")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "owned-fixture-only")
    monkeypatch.setenv("AWS_CA_BUNDLE", str(wire["cert"]))
    return wire


def import_uploaded(owner, wire):
    import ssl

    row = service.begin_artifact(organization_id=owner.org.guid, name="Tiny local model", files=files())
    row, uploads = service.artifact_uploads(row.guid, row.version)
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(wire["cert"])))
    )
    for file in uploads:
        with opener.open(
            urllib.request.Request(
                file["url"], data=contents()[file["name"]], headers=file["headers"], method="PUT"
            ),
            timeout=2,
        ) as response:
            assert response.status == 200
    return row


def test_real_import_source_tuple_and_private_delivery(owner, configured):
    with caller(owner):
        row = import_uploaded(owner, configured)
        old_version = row.version
        row = service.finalize_artifact(row.guid, old_version)
        assert row.state == "verified" and row.verified_at is not None
        assert row.organization_id == owner.org.pk and row.version > old_version
        assert all(file["version_id"].startswith("version-") for file in row.storage_receipt.values())
        assert "http" not in json.dumps(row.manifest) + json.dumps(row.storage_receipt)
        cfg = service.validate_artifact_request(owner.org.pk, row.guid, row.version)
        assert cfg["model_source"] == "local_artifact" and cfg["model_artifact_version"] == row.version
        assert "model_revision" not in cfg and "hf_token_secret_ref" not in cfg
        assert service.finalize_artifact(row.guid, row.version).version == row.version
        with pytest.raises(ValueError, match="write grants"):
            service.artifact_uploads(row.guid, row.version)
        model = SimpleNamespace(
            config=cfg,
            organization_id=owner.org.pk,
            guid=uuid4(),
            tenant_cluster=SimpleNamespace(guid=uuid4()),
        )
        plan = service.prepare_artifact_delivery(model, checkpoint=lambda: service.import_authority())
        assert all("versionId=" in file["url"] for file in plan["files"])
        assert "url" not in json.dumps(cfg)


@pytest.mark.parametrize(
    "failure", ["foreign_org", "narrow_token", "withdrawn_member", "revoked_token", "team_ceiling"]
)
def test_refused_authority_has_no_store_network(owner, configured, failure):
    foreign = Organization.objects.create(name="Other", slug="local-model-foreign")
    if failure == "narrow_token":
        owner.token.scopes = ["read:clusters", "write:clusters", "manage:clusters"]
        owner.token.save()
    if failure == "withdrawn_member":
        Member.objects.filter(pk=owner.member.pk).update(is_active=False)
    if failure == "revoked_token":
        ApiToken.objects.filter(pk=owner.token.pk).update(is_revoked=True)
    if failure == "team_ceiling":
        from astrolift_identity.models import Team

        owner.token.team = Team.objects.create(organization=owner.org, name="Scoped", slug="local-model-team")
        owner.token.save()
    with caller(owner), pytest.raises((ValueError, PermissionDenied)):
        service.begin_artifact(
            organization_id=foreign.guid if failure == "foreign_org" else owner.org.guid,
            name="Blocked",
            files=files(),
        )
    assert not configured["state"]["calls"]
    assert not LocalModelArtifact.objects.exists()


def test_missing_file_leaves_uploading_and_retry_finalizes(owner, configured):
    with caller(owner):
        row = import_uploaded(owner, configured)
        state = configured["state"]
        key = next(iter(state["objects"]))
        body = state["objects"].pop(key)
        with pytest.raises(ArtifactStoreUnavailable):
            service.finalize_artifact(row.guid, row.version)
        row.refresh_from_db()
        assert row.state == "uploading" and row.verified_at is None
        assert all("version_id" not in receipt for receipt in row.storage_receipt.values())
        state["objects"][key] = body
        assert service.finalize_artifact(row.guid, row.version).state == "verified"


def test_source_change_and_soft_deletion_cannot_deliver(owner, configured, settings):
    with caller(owner):
        row = import_uploaded(owner, configured)
        row = service.finalize_artifact(row.guid, row.version)
        cfg = service.validate_artifact_request(owner.org.pk, row.guid, row.version)
        model = SimpleNamespace(
            config=cfg,
            organization_id=owner.org.pk,
            guid=uuid4(),
            tenant_cluster=SimpleNamespace(guid=uuid4()),
        )
        count = len(configured["state"]["calls"])
        settings.AWS_STORAGE_BUCKET_NAME = "other-models"
        with pytest.raises(ArtifactStoreUnavailable, match="source changed"):
            service.prepare_artifact_delivery(model, checkpoint=lambda: service.import_authority())
        assert len(configured["state"]["calls"]) == count
        row.soft_delete()
        with pytest.raises(ValueError):
            service.prepare_artifact_delivery(model, checkpoint=lambda: service.import_authority())
        assert len(configured["state"]["calls"]) == count


def test_unconfigured_refusal_before_source_creation(owner, settings):
    settings.AWS_STORAGE_BUCKET_NAME = ""
    with caller(owner), pytest.raises(ArtifactStoreUnavailable, match="configured"):
        service.begin_artifact(organization_id=owner.org.guid, name="Unavailable", files=files())
    assert not LocalModelArtifact.objects.exists()


@pytest.mark.parametrize("expected", [None, True, 0, 999])
def test_unreviewed_source_version_refuses_before_store(owner, configured, expected):
    with caller(owner):
        row = import_uploaded(owner, configured)
        count = len(configured["state"]["calls"])
        with pytest.raises(ValueError):
            service.artifact_uploads(row.guid, expected)
        assert len(configured["state"]["calls"]) == count


def test_public_bucket_refuses_source_creation(owner, configured):
    configured["state"]["public_block"] = False
    with caller(owner), pytest.raises(ArtifactStoreUnavailable, match="public-access"):
        service.begin_artifact(organization_id=owner.org.guid, name="Unconfigured", files=files())
    assert not LocalModelArtifact.objects.exists()


def test_membership_withdrawal_between_actual_head_requests_refuses_finalization(owner, configured):
    from django.db import close_old_connections

    with caller(owner):
        row = import_uploaded(owner, configured)

        def withdraw():
            close_old_connections()
            Member.objects.filter(pk=owner.member.pk).update(is_active=False)
            close_old_connections()
            configured["state"]["after_head"] = None

        configured["state"]["after_head"] = withdraw
        before = len([call for call in configured["state"]["calls"] if call[0] == "HEAD"])
        with pytest.raises(PermissionDenied):
            service.finalize_artifact(row.guid, row.version)
        after = len([call for call in configured["state"]["calls"] if call[0] == "HEAD"])
        assert after - before == 1
        row.refresh_from_db()
        assert row.state == "uploading" and row.verified_at is None


def test_native_graphql_size_string_pagination_and_safe_metadata(owner, configured):
    schema = strawberry.Schema(query=ModelArtifactsQuery, mutation=ModelArtifactsMutation)
    with caller(owner):
        response = schema.execute_sync(
            "mutation($input:BeginLocalModelArtifactInput!){beginLocalModelArtifact(input:$input){ok errors{code} data{id version state sizeBytes}}}",
            variable_values={
                "input": {
                    "organizationId": str(owner.org.guid),
                    "name": "Big shard",
                    "files": [
                        {
                            "name": f["name"],
                            "sha256": f["sha256"],
                            "sizeBytes": str(
                                3 * 1024**3 if f["name"].endswith(".safetensors") else f["size_bytes"]
                            ),
                        }
                        for f in files()
                    ],
                }
            },
            context_value=SimpleNamespace(request=SimpleNamespace(user=owner.user)),
        )
        assert response.errors is None and response.data["beginLocalModelArtifact"]["ok"]
        assert int(response.data["beginLocalModelArtifact"]["data"]["sizeBytes"]) > 2**31
        for i in range(2):
            service.begin_artifact(organization_id=owner.org.guid, name=f"Paged source {i}", files=files())
        first = schema.execute_sync(
            "{astroliftLocalModelArtifactsPage(limit:1){items{id name state} nextCursor totalCount}}"
        )
        assert not first.errors and first.data["astroliftLocalModelArtifactsPage"]["totalCount"] == 3
        cursor = first.data["astroliftLocalModelArtifactsPage"]["nextCursor"]
        next_page = schema.execute_sync(
            "query($cursor:String!){astroliftLocalModelArtifactsPage(limit:2,after:$cursor){items{id} nextCursor}}",
            variable_values={"cursor": cursor},
        )
        assert not next_page.errors and len(next_page.data["astroliftLocalModelArtifactsPage"]["items"]) == 2
        assert "uploadUrl" not in json.dumps(first.data)


def test_native_graphql_upload_grants_and_complete_verification_envelope(owner, configured):
    import ssl

    schema = strawberry.Schema(query=ModelArtifactsQuery, mutation=ModelArtifactsMutation)
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(configured["cert"])))
    )
    with caller(owner):
        row = service.begin_artifact(organization_id=owner.org.guid, name="API source", files=files())
        variables = {"input": {"id": str(row.guid), "expectedVersion": row.version}}
        granted = schema.execute_sync(
            "mutation($input:LocalModelArtifactIdentityInput!){authorizeLocalModelUploads(input:$input){ok errors{code} data{artifact{id version state} expiresInSeconds files{name uploadUrl headers{name value}}}}}",
            variable_values=variables,
        )
        assert not granted.errors and granted.data["authorizeLocalModelUploads"]["ok"]
        data = granted.data["authorizeLocalModelUploads"]["data"]
        assert data["artifact"]["state"] == "uploading" and data["expiresInSeconds"] == 900
        for file in data["files"]:
            request = urllib.request.Request(
                file["uploadUrl"],
                data=contents()[file["name"]],
                method="PUT",
                headers={header["name"]: header["value"] for header in file["headers"]},
            )
            with opener.open(request, timeout=2) as response:
                assert response.status == 200
        finalized = schema.execute_sync(
            "mutation($input:LocalModelArtifactIdentityInput!){finalizeLocalModelArtifact(input:$input){ok errors{code} data{id version state manifestSha256}}}",
            variable_values=variables,
        )
        assert not finalized.errors and finalized.data["finalizeLocalModelArtifact"]["ok"]
        verified = finalized.data["finalizeLocalModelArtifact"]["data"]
        assert verified["id"] == str(row.guid) and verified["state"] == "verified"
        variables["input"]["expectedVersion"] = verified["version"]
        refused = schema.execute_sync(
            "mutation($input:LocalModelArtifactIdentityInput!){authorizeLocalModelUploads(input:$input){ok errors{code} data{expiresInSeconds}}}",
            variable_values=variables,
        )
        assert not refused.errors and not refused.data["authorizeLocalModelUploads"]["ok"]
        assert refused.data["authorizeLocalModelUploads"]["data"] is None
        assert "http" not in json.dumps(finalized.data) + json.dumps(refused.data)
