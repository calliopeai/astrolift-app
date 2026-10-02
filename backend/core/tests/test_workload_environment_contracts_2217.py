"""Exact workload targets against PostgreSQL and optional expendable Kind clusters.

No permission resolver, provider driver, or Kubernetes call is replaced. The
provider cases require both ASTROLIFT_WORKLOAD_TEST_KUBECONFIG_A and _B, with
contexts named kind-astrolift-env-target-2217-{a,b}; never an ambient kubeconfig.
"""

from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.utils import timezone

from astrolift_clusters.models import ProviderPlugin
from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Member, Organization, Policy
from astrolift_lifecycle.models import AppEnvironment
from astrolift_lifecycle.schema.mutations import LifecycleMutation, RestartWorkloadInput, ScaleWorkloadInput
from astrolift_registry.models import Workload
from astrolift_registry.schema.queries import RegistryQuery
from astrolift_registry.viewer_actions import workload_viewer_permissions
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    world = ScopeWorld("targets-2217")
    world.user = make_user("targets-2217")
    Member.objects.create(user=world.user, scope_kind="ORG", scope_id=world.org.pk)
    world.cluster_a = make_cluster(world, "targets-2217-a")
    world.cluster_b = make_cluster(world, "targets-2217-b")
    world.workload = Workload.objects.create(
        registered_app=world.medops_app, slug="web", name="Web", kind="deployment"
    )
    world.primary = AppEnvironment.objects.create(
        registered_app=world.medops_app,
        tenant_cluster=world.cluster_a,
        name="production",
        k8s_namespace="target-primary-2217",
        deploy_config={"max_replicas": 1},
    )
    world.selected = AppEnvironment.objects.create(
        registered_app=world.medops_app,
        tenant_cluster=world.cluster_a,
        name="staging",
        k8s_namespace="target-selected-2217",
        deploy_config={"max_replicas": 4},
    )
    world.remote = AppEnvironment.objects.create(
        registered_app=world.medops_app,
        tenant_cluster=world.cluster_b,
        name="development",
        k8s_namespace="target-remote-2217",
        deploy_config={"max_replicas": 3},
    )
    world.binding = bind_role(
        world.user,
        permissions=[Permission.APP_READ, Permission.APP_DEPLOY],
        kind="APP",
        scope_id=world.medops_app.pk,
        slug="workload-target-owner",
    )
    return world


@contextmanager
def subject(world, token=None):
    state = set_current_api_token(token)
    try:
        with tenant_context(TenantContext(organization_id=world.org.pk, actor_user_id=world.user.pk)):
            yield
    finally:
        reset_current_api_token(state)


def reviewed(world, environment=None):
    environment = environment or world.selected
    environment.refresh_from_db()
    world.workload.refresh_from_db()
    with subject(world):
        target = RegistryQuery().astrolift_workload_action_target(
            make_info(world.user),
            workload_id=str(world.workload.guid),
            environment_id=str(environment.guid),
        )
    assert target is not None
    return target


def input_for(target, action, *, replicas=2):
    fields = {
        "workload_id": target.workload_id,
        "environment_id": target.environment_id,
        "if_match_environment_version": target.environment_version,
        "expected_cluster_id": target.cluster_id,
        "if_match_cluster_version": target.cluster_version,
        "if_match_app_version": target.app_version,
        "expected_namespace": target.namespace,
    }
    if action == "scale":
        return ScaleWorkloadInput(**fields, replicas=replicas)
    return RestartWorkloadInput(**fields)


def invoke(world, target, action, *, input=None, token=None, version=None):
    with subject(world, token):
        return getattr(LifecycleMutation(), f"{action}_astrolift_workload")(
            make_info(world.user),
            input or input_for(target, action),
            if_match_version=target.workload_version if version is None else version,
        )


def code(result):
    assert not result.ok
    error = result.errors[0].code
    return getattr(error, "value", error)


def test_review_exact_same_cluster_environment_and_primary_compatibility(world):
    exact = reviewed(world)
    with subject(world):
        primary = RegistryQuery().astrolift_workload_action_target(
            make_info(world.user),
            workload_id=str(world.workload.guid),
        )
        decision = workload_viewer_permissions([world.workload])[world.workload.pk]
    assert exact.environment_id == str(world.selected.guid)
    assert exact.cluster_id == str(world.cluster_a.guid)
    assert exact.namespace == world.selected.k8s_namespace
    assert exact.viewer_can.scale.allowed
    assert primary.environment_id == str(world.primary.guid)
    assert decision.target.environment_id == str(world.primary.guid)
    assert exact.namespace != primary.namespace


@pytest.mark.parametrize("action", ["restart", "scale"])
@pytest.mark.parametrize(
    "invalid",
    [
        "deleted",
        "replaced_same_name",
        "missing",
        "foreign_app",
        "foreign_org",
        "env_version",
        "app_version",
        "cluster_version",
        "cluster_mapping",
        "namespace_mapping",
        "workload_version",
    ],
)
def test_reviewed_target_rejects_changes_before_any_provider_call(world, action, invalid):
    target = reviewed(world)
    initial = world.workload.version
    if invalid == "deleted":
        world.selected.deleted_at = timezone.now()
        world.selected.save()
    elif invalid == "replaced_same_name":
        name = world.selected.name
        world.selected.delete()
        AppEnvironment.objects.create(
            registered_app=world.medops_app,
            tenant_cluster=world.cluster_a,
            name=name,
            k8s_namespace="replacement-2217",
        )
    elif invalid == "missing":
        target.environment_id = str(uuid4())
    elif invalid == "foreign_app":
        target.environment_id = str(
            AppEnvironment.objects.create(
                registered_app=world.platform_app,
                tenant_cluster=world.cluster_a,
                name="staging",
            ).guid
        )
    elif invalid == "foreign_org":
        foreign = Organization.objects.create(name="Other", slug="target-foreign-2217")
        world.cluster_a.organization = foreign
        world.cluster_a.save()
    elif invalid == "env_version":
        world.selected.save()
    elif invalid == "app_version":
        world.medops_app.save()
    elif invalid == "cluster_version":
        world.cluster_a.save()
    elif invalid == "cluster_mapping":
        # An out-of-band writer that forgets to increment version must also fail.
        AppEnvironment.objects.filter(pk=world.selected.pk).update(tenant_cluster=world.cluster_b)
    elif invalid == "namespace_mapping":
        AppEnvironment.objects.filter(pk=world.selected.pk).update(k8s_namespace="moved-namespace-2217")
    else:
        world.workload.save()
    result = invoke(world, target, action)
    assert code(result) in {"PRECONDITION", "VERSION_MISMATCH", "PERMISSION_DENIED"}
    world.workload.refresh_from_db()
    assert world.workload.version == initial + (invalid == "workload_version")
    # Both synthetic plugins are genuinely unregistered: reaching a provider
    # would instead produce a driver error. These guards must finish earlier.


@pytest.mark.parametrize("action", ["restart", "scale"])
@pytest.mark.parametrize(
    "missing",
    [
        "if_match_environment_version",
        "if_match_cluster_version",
        "if_match_app_version",
        "expected_cluster_id",
        "expected_namespace",
    ],
)
def test_explicit_target_requires_complete_review_contract(world, action, missing):
    target = reviewed(world)
    input = input_for(target, action)
    setattr(input, missing, None)
    assert code(invoke(world, target, action, input=input)) == "VALIDATION"


@pytest.mark.parametrize("action", ["restart", "scale"])
def test_explicit_target_requires_workload_version(world, action):
    target = reviewed(world)
    with subject(world):
        result = getattr(LifecycleMutation(), f"{action}_astrolift_workload")(
            make_info(world.user),
            input_for(target, action),
        )
    assert code(result) == "VALIDATION"


@pytest.mark.parametrize("action", ["restart", "scale"])
@pytest.mark.parametrize("scopes", [["read:apps"], ["write:apps"]])
def test_current_bearer_ceiling_and_revocation_precede_dispatch(world, action, scopes):
    target = reviewed(world)
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="Target contract",
        token_hash="target-contract",
        scopes=scopes,
        is_revoked=scopes == ["write:apps"],
    )
    assert code(invoke(world, target, action, token=token)) in {"PERMISSION_DENIED", "UNAUTHORIZED"}


def test_exact_environment_policy_does_not_reuse_primary_advisory(world):
    Policy.objects.create(
        organization=world.org,
        slug="deny-selected-target",
        name="Deny staging",
        scope_level="ORG",
        action_pattern="app.deploy",
        resource_pattern={"env": ["staging"]},
    )
    target = reviewed(world)
    with subject(world):
        primary = workload_viewer_permissions([world.workload])[world.workload.pk]
    assert primary.allowed
    assert not target.viewer_can.restart.allowed
    assert code(invoke(world, target, "restart")) == "PERMISSION_DENIED"


def test_invalid_primary_review_never_silently_selects_later_environment(world):
    world.cluster_a.is_active = False
    world.cluster_a.save()
    with subject(world):
        result = RegistryQuery().astrolift_workload_action_target(
            make_info(world.user),
            workload_id=str(world.workload.guid),
        )
    assert result is None
    assert reviewed(world, world.remote).environment_id == str(world.remote.guid)


@pytest.fixture
def kind_targets(world):
    import os

    paths = [os.environ.get(f"ASTROLIFT_WORKLOAD_TEST_KUBECONFIG_{letter}") for letter in "AB"]
    if not all(paths):
        pytest.skip("requires two explicitly configured expendable Kind clusters")
    import yaml
    from kubernetes import client, config

    apis = []
    plugin = ProviderPlugin.objects.filter(slug="k8s_native").first()
    if plugin is None:
        ProviderPlugin.objects.bulk_create(
            [
                ProviderPlugin(
                    name="Native Kubernetes",
                    slug="k8s_native",
                    plugin_version="1.0.0",
                    capabilities_manifest={},
                    config_schema={},
                )
            ]
        )
        plugin = ProviderPlugin.objects.get(slug="k8s_native")
    for letter, path, cluster in zip("ab", paths, [world.cluster_a, world.cluster_b], strict=True):
        with open(path) as stream:
            kubeconfig = yaml.safe_load(stream)
        expected_context = f"kind-astrolift-env-target-2217-{letter}"
        assert kubeconfig["current-context"] == expected_context, "refusing a non-task-owned context"
        configuration = client.Configuration()
        config.load_kube_config(
            config_file=path, context=expected_context, client_configuration=configuration
        )
        assert configuration.host.startswith("https://127.0.0.1:"), "refusing a non-local API endpoint"
        api_client = client.ApiClient(configuration=configuration)
        cluster.provider_plugin = plugin
        cluster.provider_config = {"kubeconfig_path": path, "context": expected_context}
        cluster.endpoint = configuration.host
        cluster.save()
        apis.append(
            SimpleNamespace(
                core=client.CoreV1Api(api_client), apps=client.AppsV1Api(api_client), client=api_client
            )
        )
    suffix = uuid4().hex[:10]
    bindings = [(world.primary, apis[0]), (world.selected, apis[0]), (world.remote, apis[1])]
    created = []
    try:
        for environment, api in bindings:
            environment.k8s_namespace = f"target-{environment.name}-{suffix}"
            environment.save()
            api.core.create_namespace(
                {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": environment.k8s_namespace}}
            )
            created.append((environment.k8s_namespace, api))
            api.apps.create_namespaced_deployment(
                environment.k8s_namespace,
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {"name": "web"},
                    "spec": {
                        "replicas": 0,
                        "selector": {"matchLabels": {"app": "test-target"}},
                        "template": {
                            "metadata": {"labels": {"app": "test-target"}},
                            "spec": {
                                "containers": [
                                    {
                                        "name": "main",
                                        "image": "registry.k8s.io/pause:3.10",
                                        "imagePullPolicy": "Never",
                                    }
                                ]
                            },
                        },
                    },
                },
            )
        yield SimpleNamespace(apis=apis, bindings=bindings)
    finally:
        for namespace, api in created:
            api.core.delete_namespace(namespace)
        for api in apis:
            api.client.close()


def live_state(kind_targets):
    return {
        environment.pk: api.apps.read_namespaced_deployment("web", environment.k8s_namespace).to_dict()
        for environment, api in kind_targets.bindings
    }


@pytest.mark.parametrize("selected", ["selected", "remote"])
@pytest.mark.parametrize("action", ["restart", "scale"])
def test_real_kubernetes_changes_only_selected_environment(world, kind_targets, selected, action):
    environment = getattr(world, selected)
    target = reviewed(world, environment)
    before = live_state(kind_targets)
    result = invoke(world, target, action)
    assert result.ok, result.errors
    assert result.data.accepted and result.data.completed is None
    assert result.data.target.environment_id == str(environment.guid)
    assert result.data.target.cluster_id == str(environment.tenant_cluster.guid)
    assert result.data.target.namespace == environment.k8s_namespace
    assert result.data.operation_id
    assert result.data.workload_version == target.workload_version + 1
    after = live_state(kind_targets)
    for other, _ in kind_targets.bindings:
        if other.pk != environment.pk:
            assert after[other.pk]["spec"] == before[other.pk]["spec"]
            assert after[other.pk]["metadata"]["generation"] == before[other.pk]["metadata"]["generation"]
    if action == "scale":
        assert after[environment.pk]["spec"]["replicas"] == 2
    else:
        assert (
            "kubectl.kubernetes.io/restartedAt"
            in after[environment.pk]["spec"]["template"]["metadata"]["annotations"]
        )


def test_real_kubernetes_selected_replica_bounds_and_legacy_primary(world, kind_targets):
    target = reviewed(world)
    assert invoke(world, target, "scale").ok  # Primary ceiling is 1, selected ceiling is 4.
    target = reviewed(world)
    before = live_state(kind_targets)
    assert code(invoke(world, target, "scale", input=input_for(target, "scale", replicas=5))) == "VALIDATION"
    assert {pk: row["spec"] for pk, row in live_state(kind_targets).items()} == {
        pk: row["spec"] for pk, row in before.items()
    }
    with subject(world):
        legacy = LifecycleMutation().scale_astrolift_workload(
            make_info(world.user),
            ScaleWorkloadInput(workload_id=str(world.workload.guid), replicas=1),
        )
    assert legacy.ok and legacy.data.target.environment_id == str(world.primary.guid)
    assert live_state(kind_targets)[world.primary.pk]["spec"]["replicas"] == 1


def test_real_kubernetes_stale_mapping_has_no_cluster_effects(world, kind_targets):
    target = reviewed(world)
    before = live_state(kind_targets)
    AppEnvironment.objects.filter(pk=world.selected.pk).update(tenant_cluster=world.cluster_b)
    result = invoke(world, target, "restart")
    assert code(result) == "PRECONDITION"
    assert {pk: row["spec"] for pk, row in live_state(kind_targets).items()} == {
        pk: row["spec"] for pk, row in before.items()
    }


@pytest.mark.parametrize("change", ["scope", "team", "expires", "actor"])
def test_refreshed_bearer_facts_cannot_reuse_captured_authority(world, change):
    target = reviewed(world)
    token = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        team=world.medops,
        name="Reviewed authority",
        token_hash="reviewed-authority",
        scopes=["write:apps"],
    )
    changes = {
        "scope": {"scopes": ["read:apps"]},
        "team": {"team": world.platform},
        "expires": {"expires_at": timezone.now() - timedelta(seconds=1)},
        "actor": {"user": make_user("other-target-2217")},
    }
    ApiToken.objects.filter(pk=token.pk).update(**changes[change])
    assert code(invoke(world, target, "restart", token=token)) == "PERMISSION_DENIED"


@pytest.mark.parametrize("field", ["environment_id", "expected_cluster_id"])
def test_malformed_target_guid_returns_validation(world, field):
    target = reviewed(world)
    input = input_for(target, "restart")
    setattr(input, field, "invalid-guid")
    assert code(invoke(world, target, "restart", input=input)) == "VALIDATION"


def test_real_http_reviewed_scale_returns_and_audits_exact_identity(world, kind_targets, client, settings):
    from astrolift_identity.api_tokens import mint_token
    from astrolift_operations.models import AuditEvent

    issued = mint_token()
    ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="HTTP exact target",
        token_hash=issued.token_hash,
        scopes=["read:apps", "write:apps"],
    )
    headers = {
        "HTTP_X_ASTROLIFT_ORGANIZATION": str(world.org.guid),
        "HTTP_AUTHORIZATION": f"Bearer {issued.plaintext}",
    }
    query = """query Review($workload: GUID!, $environment: GUID!) {
      astroliftWorkloadActionTarget(workloadId: $workload, environmentId: $environment) {
        workloadId workloadVersion appId appVersion environmentId environmentVersion
        clusterId clusterVersion namespace viewerCan { scale { allowed } }
      }
    }"""
    response = client.post(
        f"/{settings.BASE_URL}gql/config/",
        content_type="application/json",
        **headers,
        data={
            "query": query,
            "variables": {
                "workload": str(world.workload.guid),
                "environment": str(world.selected.guid),
            },
        },
    )
    body = response.json()
    assert response.status_code == 200 and not body.get("errors"), body
    target = body["data"]["astroliftWorkloadActionTarget"]
    assert target["viewerCan"]["scale"]["allowed"]
    mutation = """mutation Scale($input: ScaleWorkloadInput!, $version: Int!) {
      scaleAstroliftWorkload(input: $input, ifMatchVersion: $version) {
        ok errors { code message } data {
          operationId accepted completed workloadVersion
          target { workloadId appId environmentId clusterId namespace }
        }
      }
    }"""
    before = live_state(kind_targets)
    response = client.post(
        f"/{settings.BASE_URL}gql/config/",
        content_type="application/json",
        **headers,
        data={
            "query": mutation,
            "variables": {
                "version": target["workloadVersion"],
                "input": {
                    "workloadId": target["workloadId"],
                    "environmentId": target["environmentId"],
                    "replicas": 2,
                    "ifMatchEnvironmentVersion": target["environmentVersion"],
                    "expectedClusterId": target["clusterId"],
                    "ifMatchClusterVersion": target["clusterVersion"],
                    "ifMatchAppVersion": target["appVersion"],
                    "expectedNamespace": target["namespace"],
                },
            },
        },
    )
    body = response.json()
    assert response.status_code == 200 and not body.get("errors"), body
    result = body["data"]["scaleAstroliftWorkload"]
    assert result["ok"], result
    data = result["data"]
    assert data["accepted"] and data["completed"] is None
    event = AuditEvent.objects.get(action="app.workload.scale", data__operation_id=data["operationId"])
    assert event.organization_id == world.org.pk
    assert event.target_id == str(world.workload.guid)
    for field in ("environment_id", "cluster_id", "namespace"):
        expected = {
            "environment_id": str(world.selected.guid),
            "cluster_id": str(world.cluster_a.guid),
            "namespace": world.selected.k8s_namespace,
        }[field]
        assert event.data[field] == expected
    after = live_state(kind_targets)
    assert after[world.selected.pk]["spec"]["replicas"] == 2
    for environment in (world.primary, world.remote):
        assert after[environment.pk]["spec"] == before[environment.pk]["spec"]
        assert (
            after[environment.pk]["metadata"]["generation"]
            == before[environment.pk]["metadata"]["generation"]
        )
