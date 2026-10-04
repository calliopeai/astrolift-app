"""Real owner grants, encrypted credential storage and bounded upstream access."""

import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError
from uuid import uuid4

import pytest
from django.db import transaction

from astrolift_graphql import GUID
from astrolift_services import hf_connection as hf
from astrolift_services.models import HuggingFaceConnection
from astrolift_services.schema.hf_connections import (
    ConnectHuggingFaceInput,
    HuggingFaceConnectionsMutation,
    HuggingFaceConnectionsQuery,
)
from astrolift_services.tests.model_hosting_helpers import promote_host_operator
from astrolift_services.tests.test_cluster_model_foundation_2213 import subject
from astrolift_services.tests.test_cluster_model_foundation_2213 import world as foundation_world
from astrolift_services.tests.test_cluster_model_queries_2213 import grant
from core.permissions import Permission
from core.secrets import encrypt_at_rest
from core.tests.utils.scope_world import make_info

pytestmark = pytest.mark.django_db
TOKEN = "hf_testcredential123456789"
SHA = "a" * 40


class Response(io.BytesIO):
    status = 200

    def __init__(self, payload):
        super().__init__(payload if isinstance(payload, bytes) else json.dumps(payload).encode())


@pytest.fixture
def world(monkeypatch):
    w = foundation_world.__wrapped__(monkeypatch)
    w.http = []
    w.responses = [{"name": "hosting-operator"}]

    def read(request, timeout):
        w.http.append((request, timeout))
        response = w.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return Response(response)

    monkeypatch.setattr(hf, "build_opener", lambda *args: SimpleNamespace(open=read))
    return w


def admin(w):
    promote_host_operator(w)
    grant(w, Permission.ORG_READ)
    grant(w, Permission.ORG_UPDATE)
    grant(w, Permission.CLUSTER_UPDATE)


def connect(w, **kwargs):
    return HuggingFaceConnectionsMutation().connect_hugging_face(
        make_info(w.user),
        input=ConnectHuggingFaceInput(
            organization_id=GUID(str(w.org.guid)),
            name="HF hosting",
            token=TOKEN,
            **kwargs,
        ),
    )


def test_connection_is_encrypted_and_safe_metadata_only(world):
    admin(world)
    with subject(world):
        result = connect(world)
        page = HuggingFaceConnectionsQuery().hugging_face_connections_page(
            make_info(world.user),
            organization_id=GUID(str(world.org.guid)),
        )
    assert result.ok and result.data.account_username == "hosting-operator"
    row = HuggingFaceConnection.objects.get()
    assert TOKEN.encode() not in bytes(row.secret_ciphertext)
    assert hf.credential(row) == TOKEN
    assert not hasattr(result.data, "token") and not hasattr(result.data, "secret_ciphertext")
    assert page.items[0].id == str(row.guid)
    request, timeout = world.http[0]
    assert request.full_url == "https://huggingface.co/api/whoami-v2" and timeout == 5
    assert request.get_header("Authorization") == f"Bearer {TOKEN}"


@pytest.mark.parametrize("permissions", [[], [Permission.CLUSTER_UPDATE], [Permission.ORG_UPDATE]])
def test_missing_administrative_capability_has_no_http_or_write(world, permissions):
    for permission in permissions:
        grant(world, permission)
    with subject(world):
        result = connect(world)
    assert not result.ok and not world.http and not HuggingFaceConnection.objects.exists()


@pytest.mark.parametrize("scopes,team", [(["org.read", "cluster.update"], None), (["admin"], "team")])
def test_actor_admin_cannot_widen_bearer_ceiling(world, scopes, team):
    admin(world)
    with subject(world, scopes=scopes, token_team=world.medops.pk if team else None):
        result = connect(world)
    assert not result.ok and not world.http and not HuggingFaceConnection.objects.exists()


def test_connection_refusal_and_upstream_diagnostics_never_disclose_token(world):
    admin(world)
    world.responses = [HTTPError("https://huggingface.co", 401, TOKEN, {}, io.BytesIO(TOKEN.encode()))]
    with subject(world):
        result = connect(world)
    assert not result.ok and TOKEN not in str(result.errors) and not HuggingFaceConnection.objects.exists()


def test_host_action_is_actual_org_decision_and_revocation_is_current(world):
    grant(world, Permission.ORG_READ)
    query = HuggingFaceConnectionsQuery()
    with subject(world):
        assert not query.model_hosting_action(make_info(world.user), GUID(str(world.org.guid))).allowed
    grant(world, Permission.ORG_UPDATE)
    grant(world, Permission.CLUSTER_UPDATE)
    with subject(world):
        assert not query.model_hosting_action(make_info(world.user), GUID(str(world.org.guid))).allowed
    promote_host_operator(world)
    with subject(world):
        assert query.model_hosting_action(make_info(world.user), GUID(str(world.org.guid))).allowed
        assert not query.model_hosting_action(make_info(world.user), GUID(str(world.other_org.guid))).allowed
    type(world.user).objects.filter(pk=world.user.pk).update(is_superuser=False)
    with subject(world):
        assert not query.model_hosting_action(make_info(world.user), GUID(str(world.org.guid))).allowed


def test_authority_revoked_during_account_read_does_not_save(world, monkeypatch):
    admin(world)

    def revoked(token):
        type(world.user).objects.filter(pk=world.user.pk).update(is_superuser=False)
        return "hosting-operator"

    monkeypatch.setattr("astrolift_services.schema.hf_connections.verify_account", revoked)
    with subject(world):
        result = connect(world)
    assert not result.ok and not HuggingFaceConnection.objects.exists()


def test_private_gated_revision_requires_separate_read_access_check(world):
    world.responses = [{"id": "owner/private", "private": True, "gated": "manual", "sha": SHA}, {}]
    model = hf.verified_model("owner/private", SHA, token=TOKEN)
    assert model.repo_id == "owner/private" and model.revision_sha == SHA
    assert [request.full_url for request, _ in world.http] == [
        f"https://huggingface.co/api/models/owner/private/revision/{SHA}",
        "https://huggingface.co/api/models/owner/private/auth-check",
    ]
    assert all(request.get_header("Authorization") == f"Bearer {TOKEN}" for request, _ in world.http)


@pytest.mark.parametrize("error", [401, 403, 404, 429, 500, 302])
def test_readable_metadata_is_not_download_permission(world, error):
    world.responses = [
        {"id": "owner/model", "sha": SHA, "gated": "manual"},
        HTTPError("", error, TOKEN, {}, None),
    ]
    with pytest.raises(hf.HuggingFaceUnavailable) as failure:
        hf.verified_model("owner/model", SHA, token=TOKEN)
    assert TOKEN not in str(failure.value)


@pytest.mark.parametrize(
    "repo,sha", [("https://other.test/model", SHA), ("owner/model", "main"), ("../model", SHA)]
)
def test_invalid_source_identity_has_zero_http(world, repo, sha):
    with pytest.raises(hf.HuggingFaceUnavailable):
        hf.verified_model(repo, sha, token=TOKEN)
    assert not world.http


def test_pinned_revision_mismatch_refuses_before_access_check(world):
    world.responses = [{"id": "owner/model", "sha": "b" * 40, "gated": False}]
    with pytest.raises(hf.HuggingFaceUnavailable):
        hf.verified_model("owner/model", SHA, token=TOKEN)
    assert len(world.http) == 1


def test_secret_bridge_only_materializes_exact_admitted_service_owner(world):
    secret = encrypt_at_rest(TOKEN.encode())
    connection = HuggingFaceConnection.objects.create(
        organization=world.org,
        name="HF",
        account_username="test",
        secret_backend_kind=secret.backend_kind,
        secret_ciphertext=secret.backend_ref,
        verified_at=world.model.created_at,
    )
    service = world.model
    service.model_hf_connection = connection
    service.model_hf_connection_version = connection.version
    path = f"services/{world.org.guid}/{service.guid}/huggingface"
    service.config = {"hf_token_secret_ref": f"{path}#token"}
    calls = []
    backend = SimpleNamespace(upsert=lambda *args: calls.append(args))
    with transaction.atomic():
        hf.materialize_model_token(service, backend)
    assert calls == [(path, {"token": TOKEN})]
    for alteration in ("version", "foreign", "deleted", "path"):
        calls.clear()
        connection.restore()
        connection.organization = world.org
        connection.save()
        service.model_hf_connection_version = connection.version
        service.config["hf_token_secret_ref"] = f"{path}#token"
        if alteration == "version":
            service.model_hf_connection_version -= 1
        elif alteration == "foreign":
            connection.organization = world.other_org
            connection.save()
        elif alteration == "deleted":
            connection.soft_delete()
        else:
            service.config["hf_token_secret_ref"] = f"services/{world.org.guid}/{uuid4()}/huggingface#token"
        with transaction.atomic(), pytest.raises(hf.HuggingFaceUnavailable):
            hf.materialize_model_token(service, backend)
        assert not calls


def test_admin_check_precedes_connection_lookup(world, monkeypatch):
    grant(world, Permission.ORG_UPDATE)
    lookups = []
    monkeypatch.setattr(
        "astrolift_services.schema.hf_connections.locked_connection", lambda *args: lookups.append(args)
    )
    from core.permissions import PermissionDenied

    with subject(world), pytest.raises(PermissionDenied):
        HuggingFaceConnectionsQuery().cluster_model_source_access(
            make_info(world.user),
            organization_id=GUID(str(world.org.guid)),
            model_repo="owner/private",
            revision_sha=SHA,
            connection_id=GUID(str(uuid4())),
            expected_connection_version=1,
        )
    assert not lookups and not world.http


@pytest.mark.parametrize("upstream_refuses", [False, True])
def test_actual_http_response_logs_and_audit_never_disclose_credential(world, caplog, upstream_refuses):
    from django.test import Client

    from astrolift_identity.api_tokens import mint_token
    from astrolift_identity.models import ApiToken, Member
    from core.schema.audit import MutationAuditLog

    admin(world)
    Member.objects.get_or_create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    minted = mint_token()
    ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="local HTTP regression",
        token_hash=minted.token_hash,
        scopes=["admin"],
    )
    if upstream_refuses:
        world.responses = [HTTPError("", 403, TOKEN, {}, io.BytesIO(TOKEN.encode()))]
    result = Client().post(
        "/app/gql/config/",
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {minted.plaintext}",
        data=json.dumps(
            {
                "query": """mutation($payload:ConnectHuggingFaceInput!){
          connectHuggingFace(input:$payload){ok data{id name accountUsername}
          errors{code message field currentVersion requestedVersion requiresAttestation supportedMethods}}}""",
                "variables": {
                    "payload": {"organizationId": str(world.org.guid), "name": "HF", "token": TOKEN}
                },
            }
        ),
    )
    assert result.status_code == 200, result.content
    body = result.json()
    assert "errors" not in body, body
    assert body["data"]["connectHuggingFace"]["ok"] is not upstream_refuses
    assert TOKEN not in result.content.decode() and TOKEN not in caplog.text
    rows = list(MutationAuditLog.objects.values("variables", "errors"))
    assert rows and TOKEN not in json.dumps(rows)
    assert rows[-1]["variables"]["payload"]["token"] == "***REDACTED***"


def test_unused_connection_disconnect_and_pinned_model_refusal(world):
    from astrolift_services.schema.hf_connections import DisconnectHuggingFaceInput

    admin(world)
    with subject(world):
        connect(world)
    row = HuggingFaceConnection.objects.get()
    world.model.model_hf_connection = row
    world.model.model_hf_connection_version = row.version
    world.model.save()

    def disconnect(version):
        return HuggingFaceConnectionsMutation().disconnect_hugging_face(
            make_info(world.user),
            input=DisconnectHuggingFaceInput(
                organization_id=GUID(str(world.org.guid)),
                connection_id=GUID(str(row.guid)),
                expected_version=version,
            ),
        )

    with subject(world):
        refused = disconnect(row.version)
    assert not refused.ok and "Deprovision" in refused.errors[0].message
    row.refresh_from_db()
    assert row.deleted_at is None
    world.model.soft_delete()
    with subject(world):
        stale = disconnect(row.version - 1)
        assert not stale.ok
        accepted = disconnect(row.version)
    assert accepted.ok
    row.refresh_from_db()
    assert row.deleted_at is not None
    assert len(world.http) == 1


@pytest.mark.parametrize("boundary", ["decrypt", "delivery"])
def test_credential_backend_failure_is_sanitized_without_secret_cause(world, monkeypatch, boundary):
    secret = encrypt_at_rest(TOKEN.encode())
    row = HuggingFaceConnection.objects.create(
        organization=world.org,
        name="HF",
        account_username="test",
        secret_backend_kind=secret.backend_kind,
        secret_ciphertext=secret.backend_ref,
        verified_at=world.model.created_at,
    )

    def unavailable(*args):
        raise RuntimeError(TOKEN)

    if boundary == "decrypt":
        monkeypatch.setattr(hf, "decrypt", unavailable)
        with pytest.raises(hf.HuggingFaceUnavailable) as failure:
            hf.credential(row)
    else:
        service = world.model
        service.model_hf_connection = row
        service.model_hf_connection_version = row.version
        service.config = {
            "hf_token_secret_ref": f"services/{world.org.guid}/{service.guid}/huggingface#token"
        }
        with transaction.atomic(), pytest.raises(hf.HuggingFaceUnavailable) as failure:
            hf.materialize_model_token(service, SimpleNamespace(upsert=unavailable))
    assert TOKEN not in str(failure.value)
    assert failure.value.__suppress_context__ and failure.value.__cause__ is None


def test_model_credential_cleanup_is_owned_and_safe_on_backend_failure(world):
    service = world.model
    service.model_hf_connection_id = 123
    path = f"services/{world.org.guid}/{service.guid}/huggingface"
    service.config = {"hf_token_secret_ref": f"{path}#token"}
    calls = []
    hf.delete_model_token(service, SimpleNamespace(delete=lambda value: calls.append(value)))
    assert calls == [path]
    service.config = {"hf_token_secret_ref": f"services/{world.org.guid}/{uuid4()}/huggingface#token"}
    with pytest.raises(hf.HuggingFaceUnavailable):
        hf.delete_model_token(service, SimpleNamespace(delete=lambda value: calls.append(value)))
    assert calls == [path]
    service.config = {"hf_token_secret_ref": f"{path}#token"}

    def failed(value):
        raise RuntimeError(TOKEN)

    with pytest.raises(hf.HuggingFaceUnavailable) as failure:
        hf.delete_model_token(service, SimpleNamespace(delete=failed))
    assert TOKEN not in str(failure.value) and failure.value.__suppress_context__


def test_upstream_plaintext_access_success_is_not_json_metadata(world):
    world.responses = [{"id": "owner/model", "sha": SHA, "gated": False}, b"OK"]
    item = hf.verified_model("owner/model", SHA, token=TOKEN)
    assert item.repo_id == "owner/model" and len(world.http) == 2


def test_malformed_json_account_refuses_without_storage(world):
    admin(world)
    world.responses = [b"not-json"]
    with subject(world):
        result = connect(world)
    assert not result.ok and not HuggingFaceConnection.objects.exists()
    assert TOKEN not in str(result.errors)


def test_status_only_access_still_bounds_body(world):
    world.responses = [{"id": "owner/model", "sha": SHA, "gated": False}, b"x" * (hf.MAX_BODY_BYTES + 1)]
    with pytest.raises(hf.HuggingFaceUnavailable):
        hf.verified_model("owner/model", SHA, token=TOKEN)
