"""Real source identity/admin admission with no local/HF source substitution."""

import hashlib
from uuid import uuid4

import pytest
from django.utils import timezone

from astrolift_clusters.models import ProviderPlugin
from astrolift_graphql import GUID
from astrolift_identity.models import Member, RoleBinding
from astrolift_services import local_model_artifacts as artifacts
from astrolift_services.models import LocalModelArtifact, ManagedService
from astrolift_services.schema.cluster_model_mutations import ClusterModelMutations
from astrolift_services.schema.model_types import cluster_model_to_type
from astrolift_services.tests.test_cluster_model_foundation_2213 import subject
from astrolift_services.tests.test_cluster_model_foundation_2213 import world as foundation_world
from astrolift_services.tests.test_cluster_model_mutations_2213 import queue as queue_fixture
from astrolift_services.tests.test_cluster_model_queries_2213 import grant, request, runtime
from astrolift_services.tests.test_local_model_artifacts import (
    caller,
    configured,  # noqa: F401 -- actual TLS SDK fixture
    import_uploaded,
    model_s3_wire,  # noqa: F401 -- actual private TLS boundary
    owner,  # noqa: F401 -- actual ORM actor fixture
)
from core.permissions import Permission
from core.tests.utils.scope_world import make_info
from providers._sdk.local_model_artifact import ModelFile, model_manifest

pytestmark = pytest.mark.django_db


@pytest.fixture
def queue(monkeypatch):
    return queue_fixture.__wrapped__(monkeypatch)


@pytest.fixture
def world(monkeypatch):
    w = foundation_world.__wrapped__(monkeypatch)
    Member.objects.get_or_create(user=w.user, scope_kind="ORG", scope_id=w.org.pk)
    actual = ProviderPlugin.objects.filter(slug="k8s_native").first()
    if actual is not None:
        w.cluster.provider_plugin = actual
    else:
        w.cluster.provider_plugin.slug = "k8s_native"
        w.cluster.provider_plugin.save()
    runtime(w)
    for declaration in w.cluster.provider_config["vllm_shared_runtimes"].values():
        declaration["image"] = "registry.test/certified-vllm@sha256:" + "a" * 64
    w.cluster.save()
    for permission in (Permission.ORG_READ, Permission.ORG_UPDATE, Permission.CLUSTER_UPDATE):
        grant(w, permission)
    manifest, digest = model_manifest(
        [
            ModelFile(name=name, size_bytes=3, sha256=hashlib.sha256(b"abc").hexdigest())
            for name in ("config.json", "tokenizer.json", "model.safetensors")
        ]
    )
    w.artifact = LocalModelArtifact.objects.create(
        organization=w.org,
        name="Local source",
        manifest=manifest,
        manifest_sha256=digest,
        state="verified",
        verified_at=timezone.now(),
    )
    w.hf = []
    monkeypatch.setattr(
        "astrolift_services.schema.hf_connections.verified_model", lambda *args, **kwargs: w.hf.append(args)
    )
    monkeypatch.setattr(
        "astrolift_services.hf_connection.verified_model", lambda *args, **kwargs: w.hf.append(args)
    )
    return w


def local_request(w, **changes):
    return request(
        w,
        name="local-host",
        model_repo=None,
        revision_sha=None,
        local_artifact_id=GUID(str(w.artifact.guid)),
        expected_artifact_version=w.artifact.version,
        **changes,
    )


def test_actual_local_provision_pins_verified_identity_without_hf_request_or_app_owner(world, queue):
    with subject(world):
        result = ClusterModelMutations().provision_cluster_model(
            make_info(world.user), input=local_request(world)
        )
    assert result.ok, result.errors
    row = ManagedService.objects.get(guid=result.data.id)
    assert row.organization_id == world.org.pk and row.tenant_cluster_id == world.cluster.pk
    assert row.registered_app_id is None and row.project_id is None
    assert row.model_hf_connection_id is None and not world.hf
    assert row.config["model_artifact_id"] == str(world.artifact.guid)
    assert row.config["model_artifact_version"] == world.artifact.version
    assert row.config["model_artifact_manifest_sha256"] == world.artifact.manifest_sha256
    assert "model_revision" not in row.config and "hf_token_secret_ref" not in row.config
    assert "url" not in str(row.config) and len(queue) == 1
    dto = cluster_model_to_type(row)
    assert dto.source_kind == "local_artifact" and dto.local_artifact_id == str(world.artifact.guid)
    assert dto.local_manifest_sha256 == world.artifact.manifest_sha256 and dto.ready is False


@pytest.mark.parametrize("change", ["foreign", "unverified", "deleted", "version", "manifest"])
def test_changed_local_source_refuses_without_hf_or_create(world, queue, change):
    item = local_request(world)
    if change == "foreign":
        world.artifact.organization = world.other_org
    elif change == "unverified":
        world.artifact.state = "uploading"
    elif change == "deleted":
        world.artifact.soft_delete()
    elif change == "version":
        item.expected_artifact_version -= 1
    else:
        world.artifact.manifest_sha256 = "f" * 64
    if change not in ("deleted", "version"):
        world.artifact.save()
        item.expected_artifact_version = world.artifact.version
    with subject(world):
        result = ClusterModelMutations().provision_cluster_model(make_info(world.user), input=item)
    assert not result.ok and not queue and not world.hf
    assert ManagedService.objects.count() == 1


@pytest.mark.parametrize("extra", ["repository", "revision", "connection", "missing_artifact"])
def test_two_sources_or_partial_source_cannot_reach_credentials_or_queue(world, queue, extra):
    item = local_request(world)
    if extra == "repository":
        item.model_repo = "owner/model"
    elif extra == "revision":
        item.revision_sha = "a" * 40
    elif extra == "connection":
        item.connection_id = GUID(str(uuid4()))
    else:
        item.local_artifact_id = None
    with subject(world):
        result = ClusterModelMutations().provision_cluster_model(make_info(world.user), input=item)
    assert not result.ok and not queue and not world.hf and ManagedService.objects.count() == 1


def test_admin_revocation_after_last_source_lock_refuses_before_create(world, queue, monkeypatch):
    original = artifacts.validate_artifact_request

    def withdraw(*args, **kwargs):
        result = original(*args, **kwargs)
        RoleBinding.objects.filter(user=world.user).delete()
        return result

    monkeypatch.setattr(artifacts, "validate_artifact_request", withdraw)
    with subject(world):
        result = ClusterModelMutations().provision_cluster_model(
            make_info(world.user), input=local_request(world)
        )
    assert not result.ok and not queue and not world.hf and ManagedService.objects.count() == 1


@pytest.mark.parametrize("change", ["owner", "manifest", "receipt"])
def test_worker_rechecks_complete_source_identity_before_private_url_generation(request, change):
    actor = request.getfixturevalue("owner")
    wire = request.getfixturevalue("configured")
    with caller(actor):
        row = import_uploaded(actor, wire)
        row = artifacts.finalize_artifact(row.guid, row.version)
        cfg = artifacts.validate_artifact_request(actor.org.pk, row.guid, row.version)
        from types import SimpleNamespace

        service = SimpleNamespace(
            config=cfg,
            organization_id=actor.org.pk,
            guid=uuid4(),
            tenant_cluster=SimpleNamespace(guid=uuid4()),
        )
        observations = []

        def checkpoint():
            observations.append(True)
            if len(observations) == 2:
                updates = (
                    {"organization_id": None}
                    if change == "owner"
                    else {"manifest": []}
                    if change == "manifest"
                    else {"storage_receipt": {}}
                )
                # Exercise a same-version concurrent transition, not a mocked DB.
                if change == "owner":
                    from astrolift_identity.models import Organization

                    updates = {
                        "organization": Organization.objects.create(name="Other", slug="worker-source-other")
                    }
                LocalModelArtifact.objects.filter(pk=row.pk).update(**updates)

        with pytest.raises(ValueError, match="changed during delivery"):
            artifacts.prepare_artifact_delivery(service, checkpoint=checkpoint)


def test_native_http_import_transitions_audit_metadata_only(request, caplog):
    actor_subject = request.getfixturevalue("owner")
    wire = request.getfixturevalue("configured")
    import json
    import ssl
    import urllib.request

    from django.test import Client

    from astrolift_identity.api_tokens import mint_token
    from astrolift_services.tests.test_local_model_artifacts import contents, files
    from core.schema.audit import MutationAuditLog

    minted = mint_token()
    actor_subject.token.token_hash = minted.token_hash
    actor_subject.token.save()
    client = Client()
    headers = {
        "HTTP_AUTHORIZATION": f"Bearer {minted.plaintext}",
        "HTTP_X_ASTROLIFT_ORGANIZATION": str(actor_subject.org.guid),
    }

    def invoke(field, input_type, payload, selection):
        response = client.post(
            "/app/gql/config/",
            content_type="application/json",
            data=json.dumps(
                {
                    "query": "mutation($payload:"
                    + input_type
                    + "!){"
                    + field
                    + "(input:$payload){ok errors{code message currentVersion} data{"
                    + selection
                    + "}}}",
                    "variables": {"payload": payload},
                }
            ),
            **headers,
        )
        assert response.status_code == 200, response.content
        result = response.json()
        assert "errors" not in result, result
        assert result["data"][field]["ok"], result
        return result["data"][field]["data"]

    begun = invoke(
        "beginLocalModelArtifact",
        "BeginLocalModelArtifactInput",
        {
            "organizationId": str(actor_subject.org.guid),
            "name": "Private source label marker",
            "files": [
                {"name": row["name"], "sizeBytes": str(row["size_bytes"]), "sha256": row["sha256"]}
                for row in files()
            ],
        },
        "id version manifestSha256",
    )
    identity = {"id": begun["id"], "expectedVersion": begun["version"]}
    authorization = invoke(
        "authorizeLocalModelUploads",
        "LocalModelArtifactIdentityInput",
        identity,
        "artifact{id version} files{name uploadUrl headers{name value}}",
    )
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(wire["cert"])))
    )
    for file in authorization["files"]:
        with opener.open(
            urllib.request.Request(
                file["uploadUrl"],
                data=contents()[file["name"]],
                headers={header["name"]: header["value"] for header in file["headers"]},
                method="PUT",
            ),
            timeout=2,
        ) as response:
            assert response.status == 200
    verified = invoke(
        "finalizeLocalModelArtifact", "LocalModelArtifactIdentityInput", identity, "id version state"
    )
    assert verified["state"] == "verified"
    records = list(MutationAuditLog.objects.order_by("id").values("operation", "variables", "errors"))
    assert [row["operation"] for row in records] == [
        "model.local_artifact.begin",
        "model.local_artifact.authorize",
        "model.local_artifact.finalize",
    ]
    observed = json.dumps(records) + caplog.text
    assert "Private source label marker" not in observed
    assert begun["manifestSha256"] not in observed
    for file in authorization["files"]:
        assert file["name"] not in observed and file["uploadUrl"] not in observed
        assert all(header["value"] not in observed for header in file["headers"] if len(header["value"]) > 10)
    assert records[0]["variables"]["payload"]["files"] == "***REDACTED***"
    assert records[0]["variables"]["payload"]["name"] == "***REDACTED***"
