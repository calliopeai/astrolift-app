"""Actual hosting authority is installation-level, independent of org owner grants."""

import json
from contextlib import contextmanager
from time import monotonic, sleep

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, transaction
from django.utils import timezone

from astrolift_graphql import GUID
from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Member
from astrolift_services.models import HuggingFaceConnection, ManagedService
from astrolift_services.schema.hf_connections import (
    ConnectHuggingFaceInput,
    HuggingFaceConnectionsMutation,
    HuggingFaceConnectionsQuery,
    require_host_admin,
)
from astrolift_services.schema.local_model_artifacts import (
    BeginLocalModelArtifactInput,
    ModelArtifactsMutation,
)
from astrolift_services.tests.test_cluster_model_foundation_2213 import subject
from astrolift_services.tests.test_model_hosting_sources import local_request
from astrolift_services.tests.test_model_hosting_sources import queue as queue_fixture
from astrolift_services.tests.test_model_hosting_sources import world as world_fixture
from core.permissions import PermissionDenied
from core.tests.utils.scope_world import make_info

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(monkeypatch):
    return world_fixture.__wrapped__(monkeypatch)


@pytest.fixture
def queue(monkeypatch):
    return queue_fixture.__wrapped__(monkeypatch)


def operator(w):
    w.user.is_superuser = True
    w.user.save(update_fields=["is_superuser"])


@contextmanager
def credential(w, token):
    with subject(w):
        marker = set_current_api_token(token)
        try:
            yield
        finally:
            reset_current_api_token(marker)


def test_full_org_owner_is_not_a_host_operator(world, queue, monkeypatch):
    from astrolift_services.schema.cluster_model_mutations import ClusterModelMutations
    from astrolift_services.schema.cluster_models import ClusterModelsQuery

    assert not world.user.is_superuser
    with subject(world):
        action = HuggingFaceConnectionsQuery().model_hosting_action(
            make_info(world.user), GUID(str(world.org.guid))
        )
        assert action.allowed is False and "platform operator" in action.reason
        result = ClusterModelMutations().provision_cluster_model(
            make_info(world.user), input=local_request(world)
        )
        assert not result.ok and result.errors[0].code == "PERMISSION_DENIED"
        with pytest.raises(PermissionDenied, match="platform operator"):
            ClusterModelsQuery().cluster_model_runtime_admission(
                make_info(world.user), input=local_request(world)
            )
        result = HuggingFaceConnectionsMutation().connect_hugging_face(
            make_info(world.user),
            input=ConnectHuggingFaceInput(
                organization_id=GUID(str(world.org.guid)), name="Hub", token="hf_fixture123456789"
            ),
        )
        assert not result.ok and result.errors[0].code == "PERMISSION_DENIED"
        result = ModelArtifactsMutation().begin_local_model_artifact(
            make_info(world.user),
            input=BeginLocalModelArtifactInput(
                organization_id=GUID(str(world.org.guid)), name="Model", files=[]
            ),
        )
        assert not result.ok and result.errors[0].code == "PERMISSION_DENIED"
    assert not queue and not world.hf and ManagedService.objects.count() == 1
    assert not HuggingFaceConnection.objects.exists()


@pytest.mark.parametrize(
    "scopes,team", [(["admin"], None), (["org:update", "clusters:write"], None), (["admin"], "team")]
)
def test_operator_bearer_still_needs_admin_and_org_ceiling(world, scopes, team):
    operator(world)
    with subject(world, scopes=scopes, token_team=world.medops.pk if team else None):
        if scopes == ["admin"] and team is None:
            require_host_admin(make_info(world.user), world.cluster)
        else:
            with pytest.raises(PermissionDenied):
                require_host_admin(make_info(world.user), world.cluster)


def test_operator_role_is_loaded_from_db_not_request_user(world):
    operator(world)
    get_user_model().objects.filter(pk=world.user.pk).update(is_superuser=False)
    assert world.user.is_superuser
    with subject(world), pytest.raises(PermissionDenied, match="platform operator"):
        require_host_admin(make_info(world.user), world.cluster)


def test_bearer_admin_scope_is_loaded_from_db_not_cached_token(world):
    operator(world)
    token = ApiToken.objects.create(
        user=world.user, organization=world.org, name="Old", token_hash="host-operator-2269", scopes=["admin"]
    )
    ApiToken.objects.filter(pk=token.pk).update(scopes=["org:update", "clusters:write"])
    with credential(world, token), pytest.raises(PermissionDenied, match="platform operator"):
        require_host_admin(make_info(world.user), world.cluster)


def test_operator_session_keeps_existing_live_org_operator_semantics(world):
    operator(world)
    Member.objects.filter(user=world.user).update(is_active=False)
    with subject(world):
        require_host_admin(make_info(world.user), world.cluster)
    with subject(world, scopes=["admin"]), pytest.raises(PermissionDenied):
        require_host_admin(make_info(world.user), world.cluster)


@pytest.mark.parametrize("operator_actor", [False, True])
def test_actual_http_host_action_distinguishes_org_owner_and_operator(world, client, operator_actor):
    if operator_actor:
        operator(world)
    minted = mint_token()
    ApiToken.objects.create(
        user=world.user, organization=world.org, name="HTTP", token_hash=minted.token_hash, scopes=["admin"]
    )
    reply = client.post(
        "/app/gql/config/",
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {minted.plaintext}",
        HTTP_X_ASTROLIFT_ORG=str(world.org.guid),
        data=json.dumps(
            {
                "query": "query($org:GUID!){modelHostingAction(organizationId:$org){allowed reason}}",
                "variables": {"org": str(world.org.guid)},
            }
        ),
    )
    assert reply.status_code == 200 and not reply.json().get("errors"), reply.json()
    assert reply.json()["data"]["modelHostingAction"]["allowed"] is operator_actor


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("withdraw", ["operator", "token_admin", "membership"])
def test_connection_lock_wait_rechecks_operator_and_credential(world, monkeypatch, withdraw):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from django.db import connection

    operator(world)
    row = HuggingFaceConnection.objects.create(
        organization=world.org,
        name="Locked",
        account_username="fixture",
        verified_at=timezone.now(),
        secret_backend_kind="encrypted",
        secret_ciphertext=b"not-read",
    )
    token = ApiToken.objects.create(
        user=world.user, organization=world.org, name="Wait", token_hash="wait-host-2269", scopes=["admin"]
    )
    locked, release, entered = Event(), Event(), Event()
    from astrolift_services.schema import hf_connections

    original = hf_connections.locked_connection
    reader_pid = []
    monkeypatch.setattr(
        "astrolift_services.schema.hf_connections.credential",
        lambda row: pytest.fail("withdrawn authority decrypted a credential"),
    )

    def observed_lock(*args):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            reader_pid.append(cursor.fetchone()[0])
        entered.set()
        return original(*args)

    monkeypatch.setattr("astrolift_services.schema.hf_connections.locked_connection", observed_lock)

    def hold():
        close_old_connections()
        try:
            with transaction.atomic():
                HuggingFaceConnection.objects.select_for_update().get(pk=row.pk)
                locked.set()
                assert entered.wait(10)
                deadline = monotonic() + 10
                while True:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_backend_pid() = ANY(pg_blocking_pids(%s))", reader_pid)
                        blocked = cursor.fetchone()[0]
                    if blocked:
                        break
                    assert monotonic() < deadline, "request did not reach the held connection row lock"
                    sleep(0.01)
                if withdraw == "operator":
                    get_user_model().objects.filter(pk=world.user.pk).update(is_superuser=False)
                elif withdraw == "token_admin":
                    ApiToken.objects.filter(pk=token.pk).update(scopes=["org:update", "clusters:write"])
                else:
                    Member.objects.filter(user=world.user).update(is_active=False)
                release.set()
        finally:
            connection.close()

    def read():
        close_old_connections()
        try:
            assert locked.wait(10)
            with credential(world, token), pytest.raises(PermissionDenied):
                HuggingFaceConnectionsQuery().cluster_model_source_access(
                    make_info(world.user),
                    organization_id=GUID(str(world.org.guid)),
                    model_repo="owner/model",
                    revision_sha="a" * 40,
                    connection_id=GUID(str(row.guid)),
                    expected_connection_version=row.version,
                )
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(hold), pool.submit(read)]
        for future in futures:
            future.result(timeout=20)
    assert release.is_set() and not world.hf


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("withdraw", ["operator", "token_admin", "membership"])
def test_artifact_final_lock_wait_refuses_withdrawn_authority(world, monkeypatch, withdraw):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from types import SimpleNamespace
    from uuid import uuid4

    from django.db import connection

    from astrolift_services import local_model_artifacts as sources
    from astrolift_services.models import LocalModelArtifact

    operator(world)
    artifact = world.artifact
    artifact.state = "uploading"
    artifact.verified_at = None
    artifact.storage_receipt = {file["name"]: {"file_id": str(uuid4())} for file in artifact.manifest}
    artifact.save()
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="Artifact wait",
        token_hash="artifact-host-wait-2269",
        scopes=["admin"],
    )
    # Storage correctness is covered by the native TLS suite. Here the real
    # PostgreSQL final source lock is the contested admission boundary.
    monkeypatch.setattr(sources, "_store", lambda *_: SimpleNamespace(verified_version=lambda *_: "v1"))
    original = sources._artifact
    locked, entered = Event(), Event()
    reader_pid = []

    def observed_source(*args, **kwargs):
        if kwargs.get("locked"):
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                reader_pid.append(cursor.fetchone()[0])
            entered.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(sources, "_artifact", observed_source)

    def hold():
        close_old_connections()
        try:
            with transaction.atomic():
                LocalModelArtifact.objects.select_for_update().get(pk=artifact.pk)
                locked.set()
                assert entered.wait(10)
                deadline = monotonic() + 10
                while True:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_backend_pid() = ANY(pg_blocking_pids(%s))", reader_pid)
                        blocked = cursor.fetchone()[0]
                    if blocked:
                        break
                    assert monotonic() < deadline, "request did not wait on the held artifact lock"
                    sleep(0.01)
                if withdraw == "operator":
                    get_user_model().objects.filter(pk=world.user.pk).update(is_superuser=False)
                elif withdraw == "token_admin":
                    ApiToken.objects.filter(pk=token.pk).update(scopes=["org:update", "clusters:write"])
                else:
                    Member.objects.filter(user=world.user).update(is_active=False)
        finally:
            connection.close()

    def finalize():
        close_old_connections()
        try:
            assert locked.wait(10)
            with credential(world, token), pytest.raises(PermissionDenied):
                sources.finalize_artifact(artifact.guid, artifact.version)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(hold), pool.submit(finalize)]
        for future in futures:
            future.result(timeout=20)
    artifact.refresh_from_db()
    assert artifact.state == "uploading" and artifact.verified_at is None
    assert all("version_id" not in row for row in artifact.storage_receipt.values())
