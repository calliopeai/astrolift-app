"""Actual GraphQL/PG existing connections; external metadata is the sole fixture seam."""

import json
from dataclasses import replace

import pytest
from aws.bedrock_catalogue import (
    BedrockCatalogueDetail,
    BedrockCatalogueIdentity,
    BedrockCataloguePage,
    BedrockCatalogueSource,
    BedrockSourceKind,
    CatalogueState,
)
from django.db import IntegrityError, transaction

from astrolift_services.models import ManagedService
from astrolift_services.native_model_connections import connection_config
from astrolift_services.tests.test_cluster_model_mutations_2213 import world as legacy_world
from astrolift_services.tests.test_model_connection_2270 import graphql_http, http_token

pytestmark = pytest.mark.django_db
ACCOUNT = "123456789012"
MODEL = "anthropic.claude-3-haiku-20240307-v1:0"
ARN = f"arn:aws:bedrock:us-east-1::foundation-model/{MODEL}"
PROFILE = f"arn:aws:bedrock:us-east-1:{ACCOUNT}:inference-profile/us.{MODEL}"
SOURCE = BedrockCatalogueSource(
    kind=BedrockSourceKind.FOUNDATION_MODEL,
    identifier=MODEL,
    arn=ARN,
    name="Haiku",
    inference_types=("ON_DEMAND",),
    lifecycle="ACTIVE",
)
DETAIL = BedrockCatalogueDetail(
    CatalogueState.METADATA, BedrockCatalogueIdentity(ACCOUNT, "us-east-1", "aws"), SOURCE
)
FIELDS = "id version name status ready runtimeSupported sourceKind nativeSource{protocol sourceKind accountId region sourceId sourceArn destinationModelArns sourceFingerprint configurationState invokeAccess}"
REGISTER = (
    "mutation($input:RegisterBedrockModelConnectionInput!){registerBedrockModelConnection(input:$input){ok errors{code currentVersion} data{"
    + FIELDS
    + "}}}"
)
DETAIL_QUERY = "query($input:BedrockModelSourceInput!){bedrockModelSource(input:$input){registerable identity{sourceFingerprint sourceArn invokeAccess}}}"


@pytest.fixture
def world(monkeypatch):
    from astrolift_clusters.models import ProviderPlugin
    from astrolift_services.tests.model_hosting_helpers import promote_host_operator

    w = legacy_world.__wrapped__(monkeypatch)
    promote_host_operator(w)
    provider, _ = ProviderPlugin.objects.get_or_create(
        slug="aws", defaults={"name": "AWS", "plugin_version": "1"}
    )
    provider.is_enabled = True
    provider.save()
    w.cluster.provider_plugin = provider
    w.cluster.provider_config = {"region": "us-east-1", "account_id": ACCOUNT}
    w.cluster.region = "us-east-1"
    w.cluster.save()
    w.cluster.refresh_from_db()
    monkeypatch.setattr("astrolift_services.native_model_connections.enabled", lambda: True)
    w.detail = DETAIL
    w.reads = []
    w.after_read = None

    class Reader:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def detail(self, kind, identifier):
            w.reads.append((kind, identifier))
            if w.after_read:
                w.after_read()
            return w.detail

        def foundation_models(self, **kwargs):
            return BedrockCataloguePage(CatalogueState.METADATA, w.detail.identity, (w.detail.source,))

        inference_profiles = foundation_models

    monkeypatch.setattr("astrolift_services.schema.bedrock_model_connections.catalogue", lambda _: Reader())
    monkeypatch.setattr("astrolift_services.native_model_connections.catalogue", lambda _: Reader())
    w.token, w.headers = http_token(w, scopes=["admin"])
    return w


def placement(w):
    return {
        "organizationId": str(w.org.guid),
        "clusterId": str(w.cluster.guid),
        "expectedProviderId": str(w.cluster.provider_plugin.guid),
        "expectedClusterVersion": w.cluster.version,
        "expectedProviderVersion": w.cluster.provider_plugin.version,
    }


def register_input(w):
    return placement(w) | {
        "sourceKind": "FOUNDATION_MODEL",
        "sourceIdentifier": ARN,
        "sourceFingerprint": connection_config(w.cluster, w.org, w.detail)["native_connection"][
            "source_fingerprint"
        ],
        "name": "Existing Bedrock",
        "allowSubscriptions": True,
    }


def register(w, client):
    value = graphql_http(client, w.headers, REGISTER, {"input": register_input(w)})
    assert not value.get("errors"), value
    result = value["data"]["registerBedrockModelConnection"]
    assert result["ok"], result
    return ManagedService.objects.get(guid=result["data"]["id"]), result["data"]


def test_metadata_then_register_no_allocation_or_ready_claim(world, client, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("existing registration reached paid/logging lifecycle")

    monkeypatch.setattr("aws.managed.model_endpoint_bedrock.AmazonBedrockDriver.provision", forbidden)
    metadata = graphql_http(
        client,
        world.headers,
        DETAIL_QUERY,
        {"input": placement(world) | {"sourceKind": "FOUNDATION_MODEL", "sourceIdentifier": ARN}},
    )
    assert metadata["data"]["bedrockModelSource"]["registerable"]
    row, data = register(world, client)
    assert data["status"] == "active" and data["ready"] is None and data["runtimeSupported"] is None
    assert data["nativeSource"]["invokeAccess"] == "unknown" and data["nativeSource"]["protocol"] == "BEDROCK"
    assert row.connection_secret_ref == "" and row.model_ready_observed_at is None
    assert row.config["native_connection"]["credential_sha256"]
    assert len(row.backend_ref) <= 512 and row.variant == "bedrock"


@pytest.mark.parametrize(
    "withdrawal",
    [
        "ordinary_admin",
        "scope",
        "team",
        "actor",
        "global",
        "provider",
        "account",
        "region",
        "version",
        "foreign_org",
        "source",
    ],
)
def test_registration_refuses_before_effects(world, client, monkeypatch, withdrawal):
    wire = register_input(world)
    if withdrawal == "ordinary_admin":
        world.user.is_superuser = False
        world.user.save()
    elif withdrawal == "scope":
        world.token.scopes = ["write:clusters"]
        world.token.save()
    elif withdrawal == "team":
        world.token.team = world.medops
        world.token.save()
    elif withdrawal == "actor":
        world.user.is_active = False
        world.user.save()
    elif withdrawal == "global":
        monkeypatch.setattr("astrolift_services.native_model_connections.enabled", lambda: False)
    elif withdrawal == "provider":
        world.cluster.provider_plugin.is_enabled = False
        world.cluster.provider_plugin.save()
    elif withdrawal == "account":
        world.cluster.provider_config["account_id"] = "999999999999"
        world.cluster.save()
    elif withdrawal == "region":
        world.cluster.provider_config["region"] = "us-west-2"
        world.cluster.save()
    elif withdrawal == "version":
        wire["expectedClusterVersion"] += 1
    elif withdrawal == "foreign_org":
        wire["organizationId"] = str(world.other_org.guid)
    else:
        wire["sourceFingerprint"] = "0" * 64
    before = ManagedService.objects.count()
    reply = client.post(
        "/app/gql/config/",
        json.dumps({"query": REGISTER, "variables": {"input": wire}}),
        content_type="application/json",
        **world.headers,
    )
    result = reply.json()
    assert (
        reply.status_code == 401
        or result.get("errors")
        or not result["data"]["registerBedrockModelConnection"]["ok"]
    ), result
    assert ManagedService.objects.count() == before


@pytest.mark.parametrize("withdrawal", ["token", "actor", "global", "credential"])
def test_registration_rechecks_after_native_transport(world, client, monkeypatch, withdrawal):
    wire = register_input(world)

    def change():
        if withdrawal == "token":
            type(world.token).objects.filter(pk=world.token.pk).update(is_revoked=True)
        elif withdrawal == "actor":
            type(world.user).objects.filter(pk=world.user.pk).update(is_superuser=False)
        elif withdrawal == "global":
            monkeypatch.setattr("astrolift_services.native_model_connections.enabled", lambda: False)
        else:
            type(world.cluster).objects.filter(pk=world.cluster.pk).update(
                provider_config={"region": "us-east-1", "account_id": "999999999999"}
            )

    world.after_read = change
    before = ManagedService.objects.count()
    result = graphql_http(client, world.headers, REGISTER, {"input": wire})
    assert not result.get("errors") and not result["data"]["registerBedrockModelConnection"]["ok"], result
    assert ManagedService.objects.count() == before


def test_owner_constraint_refuses_missing_connection_discriminator(world):
    with pytest.raises(IntegrityError), transaction.atomic():
        ManagedService.objects.create(
            organization=world.org,
            tenant_cluster=world.cluster,
            kind="model_endpoint",
            variant="bedrock",
            name="untyped",
        )


def test_entry_support_is_cloud_free_and_selected_action_is_still_required(world, client, flags):
    flags.BEDROCK_MODEL_CONNECTIONS_ENABLED = True
    support = graphql_http(
        client,
        world.headers,
        "query($org:GUID!){bedrockModelConnectionSupport(organizationId:$org){enabled allowed reason}}",
        {"org": str(world.org.guid)},
    )
    assert support["data"]["bedrockModelConnectionSupport"] == {
        "enabled": True,
        "allowed": True,
        "reason": None,
    }
    assert not world.reads
    query = "query($input:BedrockModelPlacementInput!){bedrockModelConnectionAction(input:$input){enabled allowed}}"
    action = graphql_http(client, world.headers, query, {"input": placement(world)})
    assert action["data"]["bedrockModelConnectionAction"]["allowed"], action
    from types import SimpleNamespace

    from core.tests.utils.scope_world import make_cluster

    foreign_cluster = make_cluster(SimpleNamespace(org=world.other_org), "native-entry-foreign")
    foreign_cluster.provider_plugin = world.cluster.provider_plugin
    foreign_cluster.save()
    foreign = placement(world) | {
        "clusterId": str(foreign_cluster.guid),
        "expectedClusterVersion": foreign_cluster.version,
    }
    action = graphql_http(client, world.headers, query, {"input": foreign})
    assert not action["data"]["bedrockModelConnectionAction"]["allowed"], action
    assert not world.reads


def test_unregister_is_local_only_even_when_external_source_disappears(world, client):
    service, _ = register(world, client)
    world.detail = BedrockCatalogueDetail(CatalogueState.NOT_FOUND)
    before = list(world.reads)
    result = graphql_http(
        client,
        world.headers,
        "mutation($input:UnregisterBedrockModelConnectionInput!){unregisterBedrockModelConnection(input:$input){ok errors{code} data{id version status}}}",
        {
            "input": {
                "organizationId": str(world.org.guid),
                "id": str(service.guid),
                "expectedClusterId": str(world.cluster.guid),
                "expectedProviderId": str(world.cluster.provider_plugin.guid),
                "ifMatchVersion": service.version,
            }
        },
    )
    assert result["data"]["unregisterBedrockModelConnection"]["ok"], result
    assert result["data"]["unregisterBedrockModelConnection"]["data"]["version"] == service.version
    assert not ManagedService.objects.filter(pk=service.pk).exists()
    assert world.reads == before


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("deleted", [False, True])
def test_owner_migration_rollback_refuses_persisted_native_rows(world, client, deleted):
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from django.db.migrations.recorder import MigrationRecorder

    row, _ = register(world, client)
    if deleted:
        row.soft_delete()
    with pytest.raises(IntegrityError, match="msvc_exactly_one_owner_scope"):
        MigrationExecutor(connection).migrate([("astrolift_services", "0036_model_connection_requests")])
    assert ("astrolift_services", "0037_native_bedrock_connection_owner") in (
        MigrationRecorder(connection).applied_migrations()
    )
    assert ManagedService.all_objects.filter(pk=row.pk).exists()


@pytest.mark.parametrize("withdrawal", ["global_flag", "credential_declaration"])
def test_native_inventory_and_detail_keep_discriminator_when_source_is_unavailable(
    world, client, monkeypatch, withdrawal
):
    service, _ = register(world, client)
    if withdrawal == "global_flag":
        monkeypatch.setattr("astrolift_services.native_model_connections.enabled", lambda: False)
    else:
        type(world.cluster).objects.filter(pk=world.cluster.pk).update(
            provider_config={"account_id": "999999999999", "region": "us-east-1"}
        )
    before = list(world.reads)
    query = (
        "query($org:GUID!,$id:GUID!){"
        "clusterModelDeploymentsPage(organizationId:$org){items{id sourceKind nativeSource{protocol} ready runtimeSupported}}"
        "clusterModelDeployment(organizationId:$org,id:$id){id sourceKind nativeSource{protocol} ready runtimeSupported}}"
    )
    result = graphql_http(client, world.headers, query, {"org": str(world.org.guid), "id": str(service.guid)})
    assert not result.get("errors"), result
    detail = result["data"]["clusterModelDeployment"]
    expected = {
        "id": str(service.guid),
        "sourceKind": "bedrock_foundation_model",
        "nativeSource": None,
        "ready": None,
        "runtimeSupported": None,
    }
    assert detail == expected
    page = result["data"]["clusterModelDeploymentsPage"]["items"]
    assert [row for row in page if row["id"] == str(service.guid)] == [expected]
    assert world.reads == before


def test_profile_destinations_and_credential_declaration_are_immutable(world, client):
    from astrolift_services.native_model_connections import current, verify_source

    profile = replace(
        SOURCE,
        kind=BedrockSourceKind.INFERENCE_PROFILE,
        identifier="us." + MODEL,
        arn=PROFILE,
        profile_type="SYSTEM_DEFINED",
        destination_model_arns=(ARN,),
    )
    world.detail = replace(DETAIL, source=profile)
    wire = register_input(world) | {"sourceKind": "INFERENCE_PROFILE", "sourceIdentifier": PROFILE}
    value = graphql_http(client, world.headers, REGISTER, {"input": wire})
    assert value["data"]["registerBedrockModelConnection"]["ok"], value
    row = ManagedService.objects.get(guid=value["data"]["registerBedrockModelConnection"]["data"]["id"])
    world.detail = replace(
        world.detail, source=replace(profile, destination_model_arns=(ARN.replace("us-east-1", "us-west-2"),))
    )
    with pytest.raises(ValueError, match="destinations changed"):
        verify_source(row)
    row.tenant_cluster.provider_config["credential"] = {
        "mode": "aws_assume_role",
        "role_arn": f"arn:aws:iam::{ACCOUNT}:role/changed",
    }
    assert not current(row)


FLAG = "models.bedrock_connections_enabled"
FLAG_MUTATION = "mutation($key:String!,$enabled:Boolean!){setFeatureFlag(key:$key,enabled:$enabled){ok errors{code} data{key enabled}}}"
FLAGS = "{astroliftServerInfo{featureFlags{key enabled} capabilities}}"


@pytest.fixture
def flags(monkeypatch):
    import sys
    from types import SimpleNamespace

    config = SimpleNamespace(BEDROCK_MODEL_CONNECTIONS_ENABLED=False)
    monkeypatch.setitem(sys.modules, "constance", SimpleNamespace(config=config))
    return config


def test_global_flag_default_off_public_and_operator_toggle(world, client, flags):
    result = client.post(
        "/app/gql/config/public/", json.dumps({"query": FLAGS}), content_type="application/json"
    ).json()
    assert not result.get("errors"), result
    info = result["data"]["astroliftServerInfo"]
    assert {row["key"]: row["enabled"] for row in info["featureFlags"]}[FLAG] is False
    assert "models.bedrock_connections" in set(info["capabilities"])
    for enabled in (True, False):
        result = graphql_http(client, world.headers, FLAG_MUTATION, {"key": FLAG, "enabled": enabled})
        assert result["data"]["setFeatureFlag"]["ok"], result
        assert flags.BEDROCK_MODEL_CONNECTIONS_ENABLED is enabled
        public = client.post(
            "/app/gql/config/public/", json.dumps({"query": FLAGS}), content_type="application/json"
        ).json()
        assert {r["key"]: r["enabled"] for r in public["data"]["astroliftServerInfo"]["featureFlags"]}[
            FLAG
        ] is enabled


@pytest.mark.parametrize(
    "withdrawal",
    ["staff", "team", "token_scope", "token_revoked", "membership", "operator", "logout", "session_revoked"],
)
def test_global_model_flag_requires_current_operator(world, client, flags, monkeypatch, withdrawal):
    from core.schema.mutations import feature_flags

    browser = withdrawal in ("logout", "session_revoked")
    if browser:
        client.force_login(world.user)
        headers = {"HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid), "HTTP_X_PLATFORM": "web"}
    else:
        headers = world.headers
    original = feature_flags.check_platform_operator
    called = False

    def withdraw(user, *, gate):
        nonlocal called
        original(user, gate=gate)
        if called:
            return
        called = True
        if withdrawal in ("staff", "operator"):
            type(world.user).objects.filter(pk=world.user.pk).update(is_superuser=False, is_staff=True)
        elif withdrawal == "team":
            type(world.token).objects.filter(pk=world.token.pk).update(team=world.medops)
        elif withdrawal == "token_scope":
            type(world.token).objects.filter(pk=world.token.pk).update(scopes=["read:clusters"])
        elif withdrawal == "token_revoked":
            type(world.token).objects.filter(pk=world.token.pk).update(is_revoked=True)
        elif withdrawal == "membership":
            from astrolift_identity.models import Member

            Member.objects.filter(user=world.user, scope_kind="ORG", scope_id=world.org.pk).update(
                is_active=False
            )
        elif withdrawal == "logout":
            from django.contrib.sessions.models import Session

            Session.objects.filter(session_key=client.session.session_key).delete()
        else:
            from django.utils import timezone

            from astrolift_identity.models import AstroliftSession

            AstroliftSession.all_objects.update_or_create(
                session_key=client.session.session_key,
                defaults={"user": world.user, "revoked_at": timezone.now()},
            )

    monkeypatch.setattr(feature_flags, "check_platform_operator", withdraw)
    result = graphql_http(client, headers, FLAG_MUTATION, {"key": FLAG, "enabled": True})
    assert result.get("errors") or not result["data"]["setFeatureFlag"]["ok"], result
    assert flags.BEDROCK_MODEL_CONNECTIONS_ENABLED is False


@pytest.mark.parametrize(
    "withdrawal",
    [
        "cluster_version",
        "provider_version",
        "cluster_org",
        "cluster_lifecycle",
        "cluster_inactive",
        "cluster_deleted",
        "provider_deleted",
    ],
)
@pytest.mark.parametrize("operation", ["register", "detail"])
def test_fresh_placement_withdrawal_after_transport_has_no_projection_or_registration(
    world, client, withdrawal, operation
):
    from django.db.models import F
    from django.utils import timezone

    wire = register_input(world)

    def change():
        if withdrawal == "provider_version":
            type(world.cluster.provider_plugin).objects.filter(pk=world.cluster.provider_plugin.pk).update(
                version=F("version") + 1
            )
        elif withdrawal == "provider_deleted":
            type(world.cluster.provider_plugin).objects.filter(pk=world.cluster.provider_plugin.pk).update(
                deleted_at=timezone.now()
            )
        else:
            changes = {
                "cluster_version": {"version": F("version") + 1},
                "cluster_org": {"organization_id": world.other_org.pk},
                "cluster_lifecycle": {"lifecycle": "observed"},
                "cluster_inactive": {"is_active": False},
                "cluster_deleted": {"deleted_at": timezone.now()},
            }
            type(world.cluster).objects.filter(pk=world.cluster.pk).update(**changes[withdrawal])

    world.after_read = change
    before = ManagedService.objects.count()
    if operation == "register":
        result = graphql_http(client, world.headers, REGISTER, {"input": wire})
        assert not result["data"]["registerBedrockModelConnection"]["ok"], result
        assert ManagedService.objects.count() == before
    else:
        wire = {
            key: value
            for key, value in wire.items()
            if key in placement(world) or key in ("sourceKind", "sourceIdentifier")
        }
        result = graphql_http(client, world.headers, DETAIL_QUERY, {"input": wire})
        assert result.get("errors") or result["data"]["bedrockModelSource"] is None, result
