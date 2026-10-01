"""Real owner, bearer and policy gates protect redacted persisted dependencies."""

import json
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.utils import timezone

from astrolift_clusters.models import ManagedDomain, ProviderPlugin
from astrolift_clusters.schema.mutations import ClustersMutation, UpdateTenantClusterInput
from astrolift_clusters.schema.queries import ClustersQuery
from astrolift_graphql import GUID
from astrolift_identity import abac
from astrolift_identity.api_tokens import mint_token, reset_current_api_token, set_current_api_token
from astrolift_identity.models import ApiToken, Member, Policy
from astrolift_lifecycle.models import AppEnvironment, CustomDomain, Deployment
from astrolift_lifecycle.schema.mutations import LifecycleMutation, StartDeploymentInput
from astrolift_registry.dependency_context import DOMAIN_LIMIT
from astrolift_registry.dependency_context import DependencyObservationState as State
from astrolift_registry.models import AppTeamAccess
from astrolift_registry.schema.queries import RegistryQuery
from core.permissions import Permission, PermissionDenied
from core.tenancy import TenantContext, tenant_context
from core.tests.utils.scope_world import ScopeWorld, bind_role, make_cluster, make_info, make_user

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr("core.documents.ProfileDocument.index_profile", classmethod(lambda *a: None))
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda *a: None))
    for method in ("cluster_health_dispatch", "cluster_certificates_dispatch", "_driver_for_cluster"):
        monkeypatch.setattr(
            "core.cluster_management." + method, lambda **kwargs: pytest.fail("live provider accessed")
        )
    w = ScopeWorld("dependencies2208")
    w.user = make_user("dependencies2208")
    w.app = w.medops_app
    w.cluster = make_cluster(w, "dependencies2208")
    w.cluster.region = "eu-west-1"
    w.cluster.endpoint = "https://PRIVATE_CLUSTER_ENDPOINT"
    w.cluster.provider_config = {"private": "PRIVATE_PROVIDER_CONFIG"}
    w.cluster.auth_config = {"token": "PRIVATE_CLUSTER_SECRET"}
    w.cluster.last_management_error = "OTHER_TENANT_INFRA_ERROR"
    w.cluster.save()
    w.env = AppEnvironment.objects.create(registered_app=w.app, tenant_cluster=w.cluster, name="production")
    w.domain = CustomDomain.objects.create(
        registered_app=w.app,
        hostname="app.example.test",
        certificate_id="PRIVATE_ARBITRARY_CERTIFICATE_REFERENCE",
        sni_cert_ref="https://user:password@provider.invalid/PRIVATE_CONNECTION_REF",
        byo_certificate_pem="PRIVATE_PEM",
        last_certificate_error="PRIVATE_PROVIDER_DIAGNOSTIC",
        validation_value="PRIVATE_VALIDATION_TOKEN",
        certificate_state="active",
    )
    CustomDomain.objects.create(registered_app=w.platform_app, hostname="SIBLING_DOMAIN.example.test")
    return w


def grant(w, kind="ORG", permissions=None):
    owner = {"APP": w.app, "TEAM": w.medops, "PROJECT": w.medops_project, "ORG": w.org}[kind]
    return bind_role(
        w.user,
        permissions=permissions or [Permission.APP_READ],
        kind=kind,
        scope_id=owner.pk,
        slug="reader2208",
    )


@contextmanager
def caller(w, token=None):
    current = set_current_api_token(token)
    try:
        with (
            tenant_context(
                TenantContext(
                    organization_id=w.org.pk,
                    actor_user_id=w.user.pk,
                    team_id=w.platform.pk,
                    project_id=w.platform_project.pk,
                )
            ),
            abac.request_attributes(abac.RequestAttributes(actor_user_id=w.user.pk)),
        ):
            yield
    finally:
        reset_current_api_token(current)


def read(w, **kwargs):
    args = {"app_id": w.app.guid, "environment_id": w.env.guid}
    args.update(kwargs)
    return RegistryQuery().astrolift_app_dependency_context(make_info(w.user), **args)


@pytest.mark.parametrize("kind", ["APP", "TEAM", "PROJECT", "ORG"])
@pytest.mark.parametrize("shared", [False, True])
def test_read_only_actual_owner_resolves_own_or_shared_context_without_write_authority(world, kind, shared):
    grant(world, kind)
    if shared:
        world.cluster.organization = None
        world.cluster.save()
    with caller(world):
        result = read(
            world,
            expected_cluster_id=world.cluster.guid,
            expected_provider_id=world.cluster.provider_plugin.guid,
        )
        assert str(result.cluster.id) == str(world.cluster.guid)
        assert str(result.cluster.provider_id) == str(world.cluster.provider_plugin.guid)
        assert str(result.environment_id) == str(world.env.guid)
        assert result.cluster.region == "eu-west-1"
        assert result.permissions.app_read_allowed
        assert not result.permissions.app_deploy_gate_allowed
        assert not result.permissions.cluster_register_gate_allowed
        assert result.cluster.heartbeat_state == State.NO_DATA
        assert result.cluster.heartbeat_observed_at is None
        assert result.cluster.heartbeat_status == "never_seen"
        assert result.live_provider_observation_state == State.UNAVAILABLE
        assert result.live_provider_observation_reason == "scoped_live_provider_observations_not_supported"
        assert result.domains_scope == "app_wide_persisted_metadata"
        assert [row.hostname for row in result.domains] == [world.domain.hostname]
        cert = result.domains[0]
        assert cert.configured_certificate_reference_present
        assert cert.certificate_metadata_state == State.NO_DATA
        assert cert.stored_certificate_expires_at is None
        assert cert.certificate_observed_at is None
        denied = ClustersMutation().update_tenant_cluster(
            make_info(world.user), UpdateTenantClusterInput(id=GUID(str(world.cluster.guid)), is_active=False)
        )
        assert not denied.ok
        deployment = LifecycleMutation().start_deployment(
            make_info(world.user),
            StartDeploymentInput(
                app_slug=world.app.slug, environment_name=world.env.name, image_tag="test:no-cloud-call"
            ),
        )
        assert not deployment.ok
        assert not Deployment.objects.filter(registered_app=world.app).exists()
        with pytest.raises(PermissionDenied):
            ClustersQuery().astrolift_cluster_health(make_info(world.user), GUID(str(world.cluster.guid)))
    world.cluster.refresh_from_db()
    assert world.cluster.is_active


def test_observations_are_source_stamped_snapshots_not_live_binding_or_expiry_proof(world):
    grant(world)
    now = timezone.now()
    world.cluster.last_heartbeat_at = now - timedelta(seconds=15)
    world.cluster.save()
    world.domain.last_checked_at = now - timedelta(days=3)
    world.domain.cert_metadata_refreshed_at = now - timedelta(days=4)
    world.domain.cert_expires_at = now + timedelta(days=12)
    world.domain.cert_observability_status = "manual"
    world.domain.save()
    with caller(world):
        result = read(world)
    assert result.cluster.heartbeat_source == "persisted_cluster_heartbeat"
    assert result.cluster.heartbeat_state == State.AVAILABLE
    assert result.cluster.heartbeat_observed_at == world.cluster.last_heartbeat_at
    assert result.domains[0].certificate_source == "persisted_custom_domain"
    assert result.domains[0].certificate_observed_at == world.domain.cert_metadata_refreshed_at
    assert result.domains[0].stored_certificate_expires_at == world.domain.cert_expires_at
    assert result.domains[0].stored_certificate_renewal_status == "manual"
    assert result.domains[0].validation_observed_at == world.domain.last_checked_at


@pytest.mark.parametrize(
    "invalid",
    [
        "app",
        "env",
        "project",
        "team",
        "org",
        "cluster",
        "provider",
        "inactive-cluster",
        "disabled-provider",
        "foreign-cluster",
        "foreign-project",
        "incoherent-team",
        "wrong-app",
    ],
)
def test_missing_deleted_foreign_or_incoherent_context_cannot_project(world, invalid):
    grant(world)
    owners = {
        "app": world.app,
        "env": world.env,
        "project": world.medops_project,
        "team": world.medops,
        "org": world.org,
        "cluster": world.cluster,
        "provider": world.cluster.provider_plugin,
    }
    if invalid in owners:
        owners[invalid].soft_delete()
    elif invalid == "inactive-cluster":
        world.cluster.is_active = False
        world.cluster.save()
    elif invalid == "disabled-provider":
        world.cluster.provider_plugin.is_enabled = False
        world.cluster.provider_plugin.save()
    elif invalid == "foreign-cluster":
        world.cluster.organization = ScopeWorld("foreign2208").org
        world.cluster.save()
    elif invalid == "foreign-project":
        world.app.project = ScopeWorld("foreign2208").medops_project
        world.app.save()
    elif invalid == "incoherent-team":
        world.app.team = world.platform
        world.app.save()
    else:
        world.env.registered_app = world.platform_app
        world.env.save()
    with caller(world):
        try:
            result = read(world)
        except PermissionDenied:
            return
    assert result is None


@pytest.mark.parametrize("kind", ["APP", "TEAM", "PROJECT", "ORG"])
def test_scope_and_environment_policy_denies_before_snapshot(world, kind, monkeypatch):
    grant(world)
    owner = {"APP": world.app, "TEAM": world.medops, "PROJECT": world.medops_project, "ORG": world.org}[kind]
    Policy.objects.create(
        organization=world.org,
        name="deny dependency read",
        slug="deny-dependencies",
        effect="DENY",
        action_pattern="app.read",
        resource_pattern={"env": ["production"]},
        scope_level=kind,
        scope_id=owner.pk,
    )
    monkeypatch.setattr(
        "astrolift_registry.schema.queries.read_dependency_context",
        lambda *args: pytest.fail("denied snapshot accessed"),
    )
    with caller(world), pytest.raises(PermissionDenied):
        read(world)


def test_selected_environment_policy_does_not_block_different_environment_or_claim_write_grant(world):
    grant(world, permissions=[Permission.APP_READ, Permission.APP_DEPLOY, Permission.CLUSTER_REGISTER])
    staging = AppEnvironment.objects.create(
        registered_app=world.app, tenant_cluster=world.cluster, name="staging", k8s_namespace="staging2208"
    )
    Policy.objects.create(
        organization=world.org,
        name="deny production",
        slug="deny-production",
        effect="DENY",
        action_pattern="app.*",
        resource_pattern={"env": ["production"]},
        scope_level="APP",
        scope_id=world.app.pk,
    )
    with caller(world):
        with pytest.raises(PermissionDenied):
            read(world)
        result = read(world, environment_id=staging.guid)
    assert result.environment_name == "staging"
    assert result.permissions.app_deploy_gate_allowed
    assert result.permissions.cluster_register_gate_allowed


@pytest.mark.parametrize(
    "scopes,team,allowed",
    [
        (["read:apps"], "own", True),
        (["read:apps"], "sibling", False),
        (["admin"], "sibling", False),
        (["write:apps"], "own", True),
        ([], "own", False),
        (["read:apps"], None, True),
    ],
)
def test_bearer_ceiling_never_widens_owner_or_exposes_write_gate(world, scopes, team, allowed):
    grant(world, permissions=[Permission.APP_READ, Permission.APP_DEPLOY, Permission.CLUSTER_REGISTER])
    token = SimpleNamespace(
        organization_id=world.org.pk,
        user_id=world.user.pk,
        team_id={"own": world.medops.pk, "sibling": world.platform.pk, None: None}[team],
        scopes=scopes,
    )
    with caller(world, token):
        if not allowed:
            with pytest.raises(PermissionDenied):
                read(world)
        else:
            result = read(world)
            assert result.permissions.app_deploy_gate_allowed == (scopes == ["write:apps"])
            assert not result.permissions.cluster_register_gate_allowed


def test_revoked_role_is_rechecked_on_every_read(world):
    binding = grant(world)
    with caller(world):
        assert read(world)
    binding.soft_delete()
    with caller(world), pytest.raises(PermissionDenied):
        read(world)


def test_retired_role_refuses_snapshot(world):
    binding = grant(world)
    binding.role.soft_delete()
    with caller(world), pytest.raises(PermissionDenied):
        read(world)


@pytest.mark.parametrize("reference", ["foreign", "deleted", "none", "own", "shared"])
def test_selected_domain_projects_only_live_coherent_zone_without_provider_config(world, reference):
    grant(world)
    domain = None
    if reference != "none":
        domain = ManagedDomain.objects.create(
            zone="SAFE_ZONE.example.test",
            dns_driver="route53",
            organization=ScopeWorld("foreigndomain2208").org
            if reference == "foreign"
            else world.org
            if reference != "shared"
            else None,
            dns_config={"credential": "PRIVATE_DOMAIN_CONFIG"},
        )
        world.env.managed_domain = domain
        world.env.save()
        if reference == "deleted":
            domain.soft_delete()
    with caller(world):
        result = read(world)
    expected = (
        State.AVAILABLE
        if reference in ("own", "shared")
        else State.NO_DATA
        if reference == "none"
        else State.UNAVAILABLE
    )
    assert result.managed_domain_state == expected
    if expected == State.AVAILABLE:
        assert str(result.managed_domain.id) == str(domain.guid)
        assert result.managed_domain.zone == domain.zone
    else:
        assert result.managed_domain is None


def test_domain_bounds_are_explicit_and_empty_is_distinct_from_unavailable(world):
    grant(world)
    world.domain.soft_delete()
    with caller(world):
        result = read(world)
        assert result.domains_state == State.NO_DATA
        assert result.domains == []
    CustomDomain.objects.bulk_create(
        [
            CustomDomain(registered_app=world.app, hostname=f"domain-{i}.example.test")
            for i in range(DOMAIN_LIMIT + 1)
        ]
    )
    with caller(world):
        result = read(world)
    assert result.domains_state == State.AVAILABLE
    assert len(result.domains) == DOMAIN_LIMIT
    assert result.domains_truncated
    assert result.domain_limit == DOMAIN_LIMIT


def test_expected_cluster_and_provider_identities_refuse_reassigned_targets(world):
    grant(world)
    old_cluster = world.cluster.guid
    old_provider = world.cluster.provider_plugin.guid
    world.env.tenant_cluster = make_cluster(world, "reassigned2208")
    world.env.save()
    with caller(world):
        assert read(world, expected_cluster_id=old_cluster) is None
        assert read(world, expected_provider_id=old_provider) is None
        assert read(
            world,
            expected_cluster_id=world.env.tenant_cluster.guid,
            expected_provider_id=world.env.tenant_cluster.provider_plugin.guid,
        )
        assert read(world, environment_id=uuid4()) is None


def test_exact_provider_reference_past_cap_and_replaced_slug_identity(world):
    ProviderPlugin.objects.bulk_create(
        [ProviderPlugin(name=f"Driver{i}", slug=f"aaa-{i:03}") for i in range(101)]
    )
    q = ClustersQuery()
    plugin = world.cluster.provider_plugin
    assert plugin.slug not in [p.slug for p in q.astrolift_provider_plugins(make_info(world.user))]
    result = q.astrolift_provider_plugin(make_info(world.user), slug=plugin.slug, expected_id=plugin.guid)
    assert str(result.id) == str(plugin.guid)
    assert result.capabilities_manifest == plugin.capabilities_manifest
    plugin.soft_delete()
    replacement = ProviderPlugin.objects.create(name="replacement", slug=plugin.slug)
    assert (
        q.astrolift_provider_plugin(make_info(world.user), slug=plugin.slug, expected_id=plugin.guid) is None
    )
    assert str(q.astrolift_provider_plugin(make_info(world.user), slug=plugin.slug).id) == str(
        replacement.guid
    )
    assert (
        q.astrolift_provider_plugin(make_info(SimpleNamespace(is_authenticated=False)), slug=plugin.slug)
        is None
    )


HTTP_QUERY = """
query($app:GUID!, $env:GUID!, $cluster:GUID!, $provider:GUID!, $slug:String!) {
  astroliftAppDependencyContext(appId:$app, environmentId:$env, expectedClusterId:$cluster, expectedProviderId:$provider) {
    appId environmentId environmentName readAt
    permissions { requiredPermission appReadAllowed appDeployGateAllowed clusterRegisterGateAllowed }
    cluster { id slug providerId providerSlug region lifecycle isActive heartbeatSource heartbeatState heartbeatStatus heartbeatObservedAt }
    managedDomain { id zone } managedDomainState
    domainsScope domainsState domainsTruncated domainLimit
    domains { id hostname isActive validationStatus validationObservedAt configuredCertificateReferencePresent certificateSource storedCertificateState certificateMetadataState certificateObservedAt storedCertificateExpiresAt storedCertificateRenewalStatus }
    liveProviderObservationState liveProviderObservationReason
  }
  astroliftProviderPlugin(slug:$slug, expectedId:$provider) { id slug name version capabilitiesManifest isEnabled }
}
"""


def http_identity(w, *, team=None, scopes=None):
    Member.objects.create(user=w.user, scope_kind="ORG", scope_id=w.org.pk)
    issued = mint_token()
    token = ApiToken.objects.create(
        user=w.user,
        organization=w.org,
        team=team,
        name="dependency HTTP",
        token_hash=issued.token_hash,
        scopes=scopes or ["read:apps"],
    )
    return token, {
        "HTTP_X_ASTROLIFT_ORGANIZATION": str(w.org.guid),
        "HTTP_AUTHORIZATION": f"Bearer {issued.plaintext}",
    }


def http_read(w, client, settings, headers, **variables):
    args = {
        "app": str(w.app.guid),
        "env": str(w.env.guid),
        "cluster": str(w.cluster.guid),
        "provider": str(w.cluster.provider_plugin.guid),
        "slug": w.cluster.provider_plugin.slug,
    }
    args.update(variables)
    return client.post(
        f"/{settings.BASE_URL}gql/config/",
        data={"query": HTTP_QUERY, "variables": args},
        content_type="application/json",
        **headers,
    )


@pytest.mark.parametrize("shared", [False, True])
def test_real_http_redaction_and_direct_mutation_denial_then_token_revocation(
    world, client, settings, shared
):
    grant(world)
    if shared:
        world.cluster.organization = None
        world.cluster.save()
    if shared:
        foreign = ScopeWorld("foreignshared2208")
        AppEnvironment.objects.create(
            registered_app=foreign.medops_app, tenant_cluster=world.cluster, name="production"
        )
        CustomDomain.objects.create(registered_app=foreign.medops_app, hostname="OTHER_TENANT.example.test")
    token, headers = http_identity(world)
    response = http_read(world, client, settings, headers)
    assert response.status_code == 200
    body = response.json()
    assert not body.get("errors"), body
    projected = body["data"]["astroliftAppDependencyContext"]
    assert projected["cluster"]["id"] == str(world.cluster.guid)
    assert projected["cluster"]["providerId"] == body["data"]["astroliftProviderPlugin"]["id"]
    assert [d["hostname"] for d in projected["domains"]] == [world.domain.hostname]
    serialized = json.dumps(body)
    for forbidden in ("PRIVATE_", "SIBLING_DOMAIN", "OTHER_TENANT", "password@", world.app.k8s_namespace):
        assert forbidden not in serialized
    result = client.post(
        f"/{settings.BASE_URL}gql/config/",
        data={
            "query": "mutation($id:GUID!){updateTenantCluster(input:{id:$id,isActive:false}){ok errors{message}}}",
            "variables": {"id": str(world.cluster.guid)},
        },
        content_type="application/json",
        **headers,
    )
    assert not result.json().get("errors"), result.json()
    assert not result.json()["data"]["updateTenantCluster"]["ok"]
    token.is_revoked = True
    token.save()
    response = http_read(world, client, settings, headers)
    assert response.status_code in (401, 403) or response.json().get("errors")
    world.cluster.refresh_from_db()
    assert world.cluster.is_active


@pytest.mark.parametrize(
    "negative",
    ["sibling-team", "wrong-org", "foreign-target", "inactive-account", "role-revoked", "read-scope-absent"],
)
def test_real_http_current_owner_account_and_credential_refusals(world, client, settings, negative):
    binding = grant(world)
    token, headers = http_identity(
        world,
        team=world.platform if negative == "sibling-team" else None,
        scopes=["read:workflows"] if negative == "read-scope-absent" else None,
    )
    variables = {}
    if negative == "wrong-org":
        headers["HTTP_X_ASTROLIFT_ORGANIZATION"] = str(ScopeWorld("foreignhttp2208").org.guid)
    elif negative == "foreign-target":
        foreign = ScopeWorld("foreignhttp2208")
        env = AppEnvironment.objects.create(
            registered_app=foreign.medops_app,
            tenant_cluster=make_cluster(foreign, "foreignhttp2208"),
            name="production",
        )
        variables = {"app": str(foreign.medops_app.guid), "env": str(env.guid)}
    elif negative == "inactive-account":
        world.user.is_active = False
        world.user.save()
    elif negative == "role-revoked":
        assert not http_read(world, client, settings, headers).json().get("errors")
        binding.soft_delete()
    response = http_read(world, client, settings, headers, **variables)
    body = response.json()
    assert (
        response.status_code in (401, 403)
        or body.get("errors")
        or body.get("data", {}).get("astroliftAppDependencyContext") is None
    )


def test_changed_environment_facts_are_rechecked_before_projection(world, monkeypatch):
    from astrolift_registry.dependency_context import read_dependency_context

    grant(world)
    Policy.objects.create(
        organization=world.org,
        name="deny changed environment",
        slug="deny-changed",
        effect="DENY",
        action_pattern="app.read",
        resource_pattern={"env": ["restricted"]},
        scope_level="ORG",
        scope_id=world.org.pk,
    )

    def changed(*args):
        world.env.name = "restricted"
        world.env.save()
        return read_dependency_context(*args)

    monkeypatch.setattr("astrolift_registry.schema.queries.read_dependency_context", changed)
    with caller(world), pytest.raises(PermissionDenied):
        read(world)


@pytest.mark.parametrize("kind", ["TEAM", "ORG"])
def test_viewer_share_respects_bearer_and_action_levels_then_revocation(world, kind):
    bind_role(
        world.user,
        permissions=[Permission.APP_READ, Permission.APP_DEPLOY],
        kind=kind,
        scope_id=world.platform.pk if kind == "TEAM" else world.org.pk,
        slug="shared-reader2208",
    )
    share = AppTeamAccess.objects.create(registered_app=world.app, team=world.platform, access_level="viewer")
    token = SimpleNamespace(
        organization_id=world.org.pk,
        user_id=world.user.pk,
        team_id=world.platform.pk,
        scopes=["read:apps", "write:apps"],
    )
    with caller(world, token):
        result = read(world)
        assert not result.permissions.app_deploy_gate_allowed
        assert not result.permissions.cluster_register_gate_allowed
    share.soft_delete()
    with caller(world, token), pytest.raises(PermissionDenied):
        read(world)


def test_public_discovery_advertises_support_without_granting_tenant_authority(world, client, settings):
    settings.DEBUG = False
    response = client.post(
        f"/{settings.BASE_URL}gql/config/public/",
        data={"query": "{astroliftServerInfo{capabilities}}"},
        content_type="application/json",
    )
    assert response.status_code == 200
    body = response.json()
    assert not body.get("errors"), body
    capabilities = body["data"]["astroliftServerInfo"]["capabilities"]
    assert "apps.dependency_context" in capabilities
    assert "providers.reference_read" in capabilities
    assert "PRIVATE_" not in json.dumps(body)
    _, headers = http_identity(world)
    refused = http_read(world, client, settings, headers).json()
    assert refused.get("errors")
    assert refused.get("data", {}).get("astroliftAppDependencyContext") is None


@pytest.mark.parametrize("field", ["dependency", "provider"])
def test_curated_public_discovery_has_no_dependency_or_provider_data_fields(world, client, settings, field):
    from config.schema_public import schema_public

    assert set(schema_public._schema.query_type.fields) == {"astroliftServerInfo"}
    assert schema_public._schema.mutation_type is None
    query = (
        f'{{astroliftProviderPlugin(slug:"{world.cluster.provider_plugin.slug}"){{id}}}}'
        if field == "provider"
        else f'{{astroliftAppDependencyContext(appId:"{world.app.guid}",environmentId:"{world.env.guid}"){{appId}}}}'
    )
    response = client.post(
        f"/{settings.BASE_URL}gql/config/public/", data={"query": query}, content_type="application/json"
    )
    assert response.status_code in (200, 400)
    assert response.json().get("errors")
    assert not response.json().get("data")
