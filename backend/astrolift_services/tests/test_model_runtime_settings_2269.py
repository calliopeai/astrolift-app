"""Real selected-owner, operator and version fences for runtime declarations."""

from dataclasses import replace
from uuid import uuid4

import pytest

from astrolift_graphql import GUID
from astrolift_services.model_runtime_settings import (
    ModelDtype,
    ModelRuntimeArchitecture,
    ModelRuntimeDeclarationInput,
    ModelRuntimeMode,
    ModelRuntimeSelectorInput,
    declaration_config,
)
from astrolift_services.schema.model_runtime_settings import (
    ModelRuntimeSettingsMutation,
    ModelRuntimeSettingsQuery,
    UpdateClusterModelRuntimeInput,
)
from astrolift_services.tests.model_hosting_helpers import promote_host_operator
from astrolift_services.tests.test_cluster_model_foundation_2213 import subject
from astrolift_services.tests.test_model_hosting_sources import world as world_fixture
from core.permissions import PermissionDenied
from core.tests.utils.scope_world import make_info

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(monkeypatch):
    return world_fixture.__wrapped__(monkeypatch)


def declaration(**changes):
    values = {
        "image": "registry.test/runtime@sha256:" + "a" * 64,
        "version": "0.15.1",
        "package_version": "0.15.1+cpu",
        "architecture": ModelRuntimeArchitecture.AMD64,
        "node_selector": [ModelRuntimeSelectorInput(key="astrolift.io/runtime", value="avx2")],
        "supported_dtypes": [ModelDtype.FLOAT32],
        "default_dtype": ModelDtype.FLOAT32,
        "default_max_model_len": 256,
        "max_model_len_ceiling": 512,
        "default_max_num_seqs": 1,
        "max_num_seqs_ceiling": 2,
        "cpu_request_ceiling": "2",
        "memory_request_ceiling": "4Gi",
        "gpu_count_ceiling": 0,
        "hardware_certified": True,
        "hardware_attested": True,
        "hardware_evidence": "Native image smoke report and operator node inspection receipt",
    }
    return ModelRuntimeDeclarationInput(**(values | changes))


def request(w, **changes):
    values = {
        "organization_id": GUID(str(w.org.guid)),
        "cluster_id": GUID(str(w.cluster.guid)),
        "expected_provider_id": GUID(str(w.cluster.provider_plugin.guid)),
        "if_match_version": w.cluster.version,
        "expected_provider_version": w.cluster.provider_plugin.version,
        "compute_mode": ModelRuntimeMode.CPU,
        "declaration": declaration(),
    }
    return UpdateClusterModelRuntimeInput(**(values | changes))


def apply(w, **changes):
    return ModelRuntimeSettingsMutation().update_cluster_model_runtime(
        make_info(w.user), input=request(w, **changes)
    )


def read(w):
    return ModelRuntimeSettingsQuery().cluster_model_runtime_settings(
        make_info(w.user),
        organization_id=GUID(str(w.org.guid)),
        cluster_id=GUID(str(w.cluster.guid)),
        expected_provider_id=GUID(str(w.cluster.provider_plugin.guid)),
    )


def test_ordinary_all_grants_owner_cannot_read_or_write_operator_setup(world):
    original = world.cluster.provider_config
    with subject(world):
        with pytest.raises(PermissionDenied):
            read(world)
        result = apply(world)
    assert not result.ok and result.errors[0].code == "PERMISSION_DENIED"
    world.cluster.refresh_from_db()
    assert world.cluster.provider_config == original


def test_runtime_setup_preserves_unrelated_config_and_other_mode_and_returns_only_safe_fields(world):
    promote_host_operator(world)
    world.cluster.provider_config = {
        "auth_token": "private-marker",
        "vllm_shared_runtimes": {"gpu": {"opaque": "unchanged"}},
    }
    world.cluster.save()
    with subject(world):
        result = apply(world)
        assert result.ok, result.errors
        observation = read(world)
    world.cluster.refresh_from_db()
    assert world.cluster.provider_config["auth_token"] == "private-marker"
    assert world.cluster.provider_config["vllm_shared_runtimes"]["gpu"] == {"opaque": "unchanged"}
    assert "private-marker" not in repr(result.data) and "private-marker" not in repr(observation)
    assert observation.cluster_version == world.cluster.version == result.data.cluster_version
    assert observation.modes[0].configured and observation.modes[0].hardware_admission == "operator_declared"
    assert observation.modes[1].declaration is None and not observation.modes[1].configured
    assert observation.modes[0].declaration.supported_dtypes == [ModelDtype.FLOAT32]


def test_unattested_setup_can_be_saved_as_unconfigured_without_certifying_hardware(world):
    promote_host_operator(world)
    with subject(world):
        result = apply(
            world,
            declaration=declaration(
                hardware_certified=False, hardware_attested=False, hardware_evidence=None
            ),
        )
    assert result.ok and not result.data.modes[0].configured
    assert not result.data.modes[0].declaration.hardware_certified


@pytest.mark.parametrize(
    "changes",
    [
        {"hardware_attested": False},
        {"hardware_evidence": None},
        {"image": "registry.test/runtime:v0.15.1"},
        {"image": "https://user:private@registry.test/runtime@sha256:" + "a" * 64},
        {"supported_dtypes": []},
        {"supported_dtypes": [ModelDtype.BFLOAT16]},
        {"default_max_model_len": 255},
        {"default_max_num_seqs": 0},
        {"max_model_len_ceiling": 131073},
        {"cpu_request_ceiling": "0"},
        {"memory_request_ceiling": "1Gi"},
        {"gpu_count_ceiling": 1},
        {"node_selector": [ModelRuntimeSelectorInput(key="kubernetes.io/arch", value="amd64")]},
    ],
)
def test_invalid_operator_declaration_is_refused_without_partial_config_write(world, changes):
    promote_host_operator(world)
    before = dict(world.cluster.provider_config)
    with subject(world):
        result = apply(world, declaration=declaration(**changes))
    assert not result.ok and result.errors[0].code == "VALIDATION", result.errors
    world.cluster.refresh_from_db()
    assert world.cluster.provider_config == before


@pytest.mark.parametrize(
    "change",
    ["cluster-version", "provider-version", "provider-id", "foreign-org", "inactive", "provider-disabled"],
)
def test_current_target_versions_and_transport_eligibility_refuse_before_writes(world, change):
    promote_host_operator(world)
    input = request(world)
    if change == "cluster-version":
        input = replace(input, if_match_version=input.if_match_version - 1)
    elif change == "provider-version":
        input = replace(input, expected_provider_version=input.expected_provider_version + 1)
    elif change == "provider-id":
        input = replace(input, expected_provider_id=GUID(str(uuid4())))
    elif change == "foreign-org":
        input = replace(input, organization_id=GUID(str(world.other_org.guid)))
    elif change == "inactive":
        world.cluster.is_active = False
        world.cluster.save()
    else:
        world.cluster.provider_plugin.is_enabled = False
        world.cluster.provider_plugin.save()
    before = dict(world.cluster.provider_config)
    with subject(world):
        result = ModelRuntimeSettingsMutation().update_cluster_model_runtime(
            make_info(world.user), input=input
        )
    assert not result.ok and result.errors[0].code in ("VERSION_MISMATCH", "PRECONDITION")
    world.cluster.refresh_from_db()
    assert world.cluster.provider_config == before


def test_runtime_controls_only_accept_declared_float32_and_bounded_requests(world):
    from astrolift_services.model_admission import validate_cluster_request
    from astrolift_services.tests.test_cluster_model_queries_2213 import request as model_request

    world.cluster.provider_config = {
        "vllm_shared_runtimes": {"cpu": declaration_config(declaration(), "cpu")}
    }
    world.cluster.save()
    input = model_request(world, cpu_request="1", memory_request="4Gi", cpu_kv_cache_gi_b=1)
    config, _ = validate_cluster_request(input, world.cluster)
    assert config["dtype"] == "float32" and config["max_model_len"] == 256 and config["max_num_seqs"] == 1
    for changes in (
        {"dtype": ModelDtype.BFLOAT16},
        {"max_model_len": 1024},
        {"max_num_seqs": 3},
        {"cpu_request": "3"},
        {"memory_request": "8Gi"},
    ):
        with pytest.raises(ValueError):
            validate_cluster_request(replace(input, **changes), world.cluster)


def test_read_time_operator_withdrawal_uses_fresh_actor_not_request_snapshot(world):
    promote_host_operator(world)
    type(world.user).objects.filter(pk=world.user.pk).update(is_superuser=False)
    with subject(world):
        result = apply(world)
    assert not result.ok and result.errors[0].code == "PERMISSION_DENIED"


@pytest.mark.parametrize("operator_actor", [True, False])
def test_actual_http_runtime_setup_has_complete_envelope_and_safe_projection(world, client, operator_actor):
    import json
    from dataclasses import asdict

    from astrolift_identity.api_tokens import mint_token
    from astrolift_identity.models import ApiToken
    from core.schema.audit import MutationAuditLog

    if operator_actor:
        promote_host_operator(world)
    minted = mint_token()
    ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="Runtime HTTP",
        token_hash=minted.token_hash,
        scopes=["admin"],
    )
    body = asdict(request(world))

    def public(value):
        if isinstance(value, dict):
            return {
                key.split("_")[0] + "".join(part.title() for part in key.split("_")[1:]): public(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [public(item) for item in value]
        return value.name if hasattr(value, "name") else value

    world.cluster.provider_config["private_runtime_marker"] = "never-public-runtime-secret-marker"
    world.cluster.save()
    body["if_match_version"] = world.cluster.version
    reply = client.post(
        "/app/gql/config/",
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {minted.plaintext}",
        HTTP_X_ASTROLIFT_ORG=str(world.org.guid),
        data=json.dumps(
            {
                "query": "mutation($input:UpdateClusterModelRuntimeInput!){updateClusterModelRuntime(input:$input){ok errors{code message currentVersion requestedVersion field requiresAttestation supportedMethods} data{clusterId clusterVersion providerVersion modes{computeMode configured hardwareAdmission declaration{image supportedDtypes defaultDtype hardwareEvidence}}}}}",
                "variables": {"input": public(body)},
            }
        ),
    )
    assert reply.status_code == 200 and not reply.json().get("errors"), reply.json()
    result = reply.json()["data"]["updateClusterModelRuntime"]
    assert result["ok"] is operator_actor
    assert "never-public-runtime-secret-marker" not in reply.content.decode()
    if not operator_actor:
        assert result["errors"][0]["code"] == "PERMISSION_DENIED"
    else:
        world.cluster.refresh_from_db()
        saved = world.cluster.provider_config["vllm_shared_runtimes"]["cpu"]
        assert saved["hardware_attested_by_user_id"] == world.user.pk and saved["hardware_attested_at"]
    records = list(MutationAuditLog.objects.filter(operation="model.runtime.configure").values())
    assert records and "never-public-runtime-secret-marker" not in repr(records)
    assert body["declaration"]["hardware_evidence"] not in repr(records)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("withdraw", ["operator", "token_admin", "membership", "provider_disabled"])
@pytest.mark.parametrize("lock_kind", ["cluster", "provider"])
def test_final_cluster_lock_rechecks_current_runtime_authority_and_placement(
    world, monkeypatch, withdraw, lock_kind
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic, sleep

    from django.db import close_old_connections, connection, transaction

    from astrolift_clusters.models import TenantCluster
    from astrolift_identity.models import ApiToken, Member
    from astrolift_services.schema import cluster_model_mutations
    from astrolift_services.tests.test_model_host_operator_2269 import credential

    promote_host_operator(world)
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="Runtime wait",
        token_hash="runtime-setup-wait",
        scopes=["admin"],
    )
    input = request(world)
    original = cluster_model_mutations._locked_cluster
    locked, entered = Event(), Event()
    reader_pid = []

    def observed(*args):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            reader_pid.append(cursor.fetchone()[0])
        entered.set()
        return original(*args)

    monkeypatch.setattr(cluster_model_mutations, "_locked_cluster", observed)
    before = dict(world.cluster.provider_config)

    def hold():
        close_old_connections()
        try:
            with transaction.atomic():
                if lock_kind == "cluster":
                    TenantCluster.objects.select_for_update().get(pk=world.cluster.pk)
                else:
                    type(world.cluster.provider_plugin).objects.select_for_update().get(
                        pk=world.cluster.provider_plugin.pk
                    )
                locked.set()
                assert entered.wait(10)
                deadline = monotonic() + 10
                while True:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_backend_pid() = ANY(pg_blocking_pids(%s))", reader_pid)
                        blocked = cursor.fetchone()[0]
                    if blocked:
                        break
                    assert monotonic() < deadline
                    sleep(0.01)
                if withdraw == "operator":
                    type(world.user).objects.filter(pk=world.user.pk).update(is_superuser=False)
                elif withdraw == "token_admin":
                    ApiToken.objects.filter(pk=token.pk).update(scopes=["org:update", "clusters:write"])
                elif withdraw == "membership":
                    Member.objects.filter(user=world.user).update(is_active=False)
                else:
                    type(world.cluster.provider_plugin).objects.filter(
                        pk=world.cluster.provider_plugin.pk
                    ).update(is_enabled=False)
        finally:
            connection.close()

    def write():
        close_old_connections()
        try:
            assert locked.wait(10)
            with credential(world, token):
                result = ModelRuntimeSettingsMutation().update_cluster_model_runtime(
                    make_info(world.user), input=input
                )
            assert not result.ok and result.errors[0].code in ("PERMISSION_DENIED", "PRECONDITION")
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(hold), pool.submit(write)]
        for future in futures:
            future.result(timeout=20)
    world.cluster.refresh_from_db()
    assert world.cluster.provider_config == before


def test_runtime_setup_capability_is_discoverable_but_not_authority():
    from core.capabilities_registry import SHIPPED_CAPABILITIES

    assert "models.runtime_settings" in SHIPPED_CAPABILITIES


@pytest.mark.parametrize("local", [False, True])
def test_updates_preserve_omitted_serving_controls_and_source_identity(world, local):
    promote_host_operator(world)
    from astrolift_services.model_admission import validate_cluster_request
    from astrolift_services.model_settings import (
        UpdateClusterModelInput,
        update_placement,
        validate_updated_source,
    )
    from astrolift_services.tests.test_cluster_model_queries_2213 import request as model_request
    from astrolift_services.tests.test_model_hosting_sources import local_request

    world.cluster.provider_config = {
        "vllm_shared_runtimes": {"cpu": declaration_config(declaration(), "cpu")}
    }
    world.cluster.save()
    source = local_request(world) if local else model_request(world)
    source = replace(
        source,
        cpu_request="1",
        memory_request="4Gi",
        cpu_kv_cache_gi_b=1,
        max_model_len=512,
        max_num_seqs=2,
        dtype=ModelDtype.FLOAT32,
    )
    with subject(world):
        config, _ = validate_cluster_request(source, world.cluster)
        config["enable_prefix_caching"] = True
        world.model.config = config
        world.model.save()
        input = UpdateClusterModelInput(
            organization_id=GUID(str(world.org.guid)),
            id=GUID(str(world.model.guid)),
            expected_cluster_id=GUID(str(world.cluster.guid)),
            expected_provider_id=GUID(str(world.cluster.provider_plugin.guid)),
            if_match_version=world.model.version,
            cpu_request="1",
            memory_request="4Gi",
            gpu_count=0,
            cpu_kv_cache_gi_b=1,
            allow_subscriptions=False,
        )
        updated, _ = validate_updated_source(world.model, update_placement(world.model, input))
        assert (
            updated["dtype"] == "float32" and updated["max_model_len"] == 512 and updated["max_num_seqs"] == 2
        )
        assert updated["enable_prefix_caching"] is True
        assert updated.get("model_artifact_id") == config.get("model_artifact_id") and updated.get(
            "model_revision"
        ) == config.get("model_revision")
        revised, _ = validate_updated_source(
            world.model, update_placement(world.model, replace(input, max_model_len=256, max_num_seqs=1))
        )
        assert revised["max_model_len"] == 256 and revised["max_num_seqs"] == 1
