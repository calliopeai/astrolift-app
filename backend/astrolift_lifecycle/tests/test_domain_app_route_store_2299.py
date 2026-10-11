"""Real PG original route reservation/evidence visibility and source fencing."""

import json
from importlib import import_module
from uuid import uuid4

import pytest
from django.db import DatabaseError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import ApiToken
from astrolift_lifecycle import domain_app_route as source
from astrolift_lifecycle import domain_app_route_store as store
from astrolift_lifecycle import domain_route_authority as authority
from astrolift_lifecycle.models import AppEnvironment, Deployment, ManagedDomainAppBinding
from astrolift_registry.models import Workload
from core.permissions import PermissionDenied

from .test_domain_route_authority_2299 import read
from .test_domain_route_authority_2299 import world as authority_world


@pytest.fixture
def world(monkeypatch):
    return authority_world.__wrapped__(monkeypatch)


pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def admitted(world, client):
    assert not read(client, world).get("errors")
    world.ref = world.refs[-1]
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="aws",
        defaults={"name": "AWS", "plugin_version": "0.0.1", "capabilities_manifest": {}, "config_schema": {}},
    )
    world.cluster = TenantCluster.objects.create(
        organization=world.org,
        provider_plugin=plugin,
        name="owned",
        slug=uuid4().hex,
        region="us-east-1",
        ingress_class="envoy",
        lifecycle="managed",
        delivery_mode="direct_api",
        endpoint="https://owned.invalid",
        auth_method="exec_plugin",
        provider_config={"cluster_name": "owned", "account_id": "123456789012"},
    )
    app = world.medops_app
    app.manifest_raw = """name = "owned"
[[workloads]]
name = "web"
kind = "faas"
is_public = true
faas_public = true
faas_package_type = "image"
"""
    app.save()
    world.env = AppEnvironment.objects.create(
        registered_app=app, tenant_cluster=world.cluster, name="production"
    )
    world.workload = Workload.objects.create(
        registered_app=app, name="web", slug="web", kind="faas", is_public=True
    )
    world.deployment = Deployment.objects.create(
        registered_app=app, app_environment=world.env, trigger_kind="manual", status="running"
    )
    from astrolift_services.models import ManagedService

    for kind, variant, suffix, handle, config in (
        ("faas", "lambda", "fn", "faas/owned", {}),
        (
            "api_gateway",
            "http_api",
            "api",
            "api_gateway/abc123",
            {"lambda_function_arn": "arn:aws:lambda:us-east-1:123456789012:function:owned"},
        ),
    ):
        ManagedService.objects.create(
            registered_app=app,
            app_environment=world.env,
            name=f"web-production-{suffix}",
            kind=kind,
            variant=variant,
            status="active",
            backend_ref=handle,
            config=config,
            applied_config=config,
        )
    with authority.current(world.ref):
        selected = source.target(
            world.domain, world.env.guid, world.env.version, world.workload.guid, reference=world.ref
        )
        world.snapshot = source.snapshot(world.domain, selected, world.domain.zone)
    world.snapshot.update(
        edge={
            "account_id": "123456789012",
            "region": "us-east-1",
            "dns_name": "owned.us-east-1.elb.amazonaws.com",
            "canonical_zone_id": "ZALB",
        },
        writer="ROUTE53_ALIAS",
        dns_present=False,
        dns_matches=False,
        can_prepare=True,
        can_attach=False,
    )
    return world


def reserve(w, request=None):
    return store.reserve(
        snapshot=w.snapshot,
        review_sha256=source.digest(w.snapshot),
        reference=w.ref,
        request_id=request or uuid4(),
        purpose="prepare",
    )


def test_committed_sent_visible_separate_pg_and_received_ack_survives_revocation(admitted):
    w = admitted
    row = reserve(w)
    intent = store.stage_sent(row.guid, "certificate", {"operation": str(row.guid), "zone": w.domain.zone})
    separate = connection.Database.connect(**connection.get_connection_params())
    try:
        with separate.cursor() as cursor:
            cursor.execute(
                "SELECT native_receipts FROM astrolift_lifecycle_manageddomainappbinding WHERE id=%s",
                [row.pk],
            )
            received = cursor.fetchone()[0]
            if isinstance(received, str):
                received = json.loads(received)
            assert received["certificate"]["phase"] == "SENT"
    finally:
        separate.close()
    ApiToken.objects.filter(pk=w.token.pk).update(is_revoked=True)
    arn = "arn:aws:acm:us-east-1:123456789012:certificate/11111111-1111-1111-1111-111111111111"
    store.retain_ack(row.guid, "certificate", intent, {"certificate_arn": arn})
    row.refresh_from_db()
    assert row.native_receipts["certificate"]["ack"]["certificate_arn"] == arn
    with pytest.raises(PermissionDenied):
        store.observed(row.guid, "certificate", intent)
    with pytest.raises(PermissionDenied):
        store.stage_sent(row.guid, "other", {"new": "effect"})


def test_outer_transaction_refuses_before_any_reservation(admitted):
    with transaction.atomic():
        with pytest.raises(store.DomainRouteStoreError, match="COMMITTED_ROUTE_TRANSACTION_REQUIRED"):
            reserve(admitted)
    assert not ManagedDomainAppBinding.objects.exists()


def test_original_request_and_certificate_preparation_cannot_be_replaced(admitted):
    request = uuid4()
    row = reserve(admitted, request)
    assert reserve(admitted, request).pk == row.pk
    with pytest.raises(store.DomainRouteStoreError, match="ORIGINAL_DOMAIN_PREPARATION"):
        reserve(admitted)
    store.stage_sent(row.guid, "certificate", {"zone": admitted.domain.zone})
    with pytest.raises(store.DomainRouteStoreError, match="ALREADY_RESERVED"):
        store.stage_sent(row.guid, "certificate", {"zone": admitted.domain.zone})


@pytest.mark.parametrize("change", ["org", "provider"])
def test_post_send_original_evidence_survives_current_owner_placement_change(admitted, change):
    w = admitted
    row = reserve(w)
    intent = store.stage_sent(row.guid, "certificate", {"zone": w.domain.zone})
    if change == "org":
        from astrolift_identity.models import Organization

        foreign = Organization.objects.create(name="Foreign", slug=uuid4().hex)
        type(w.medops_app).all_objects.filter(pk=w.medops_app.pk).update(organization=foreign)
    else:
        plugin = ProviderPlugin.objects.create(
            name="other", slug=uuid4().hex, plugin_version="0.0.1", capabilities_manifest={}, config_schema={}
        )
        TenantCluster.all_objects.filter(pk=w.cluster.pk).update(provider_plugin=plugin)
    store.retain_ack(row.guid, "certificate", intent, {"certificate_arn": "original-received-arn"})
    row.refresh_from_db()
    assert row.native_receipts["certificate"]["phase"] == "ACK"
    with pytest.raises((PermissionDenied, source.DomainAppRouteError, store.DomainRouteStoreError)):
        store.observed(row.guid, "certificate", intent)


def test_db_immutable_original_owner_and_history_and_reverse_refusal(admitted):
    row = reserve(admitted)
    with pytest.raises(DatabaseError), transaction.atomic():
        ManagedDomainAppBinding.all_objects.filter(pk=row.pk).update(hostname="changed.example.test")
    with pytest.raises(DatabaseError), transaction.atomic():
        ManagedDomainAppBinding.all_objects.filter(pk=row.pk).delete()
    row.soft_delete()
    assert ManagedDomainAppBinding.all_objects.get(pk=row.pk).deleted_at is not None
    module = import_module("astrolift_lifecycle.migrations.0046_managed_domain_app_binding")
    executor = MigrationExecutor(connection)
    with connection.schema_editor(atomic=True) as editor:
        with pytest.raises(RuntimeError, match="empty retained"):
            module.remove_original_guards(executor.loader.project_state().apps, editor)
    assert ManagedDomainAppBinding.all_objects.get(pk=row.pk).hostname == admitted.domain.zone


@pytest.fixture
def admitted_faas(admitted):
    w = admitted
    w.medops_app.manifest_raw = """name = "owned"
[[workloads]]
name = "web"
kind = "faas"
is_public = true
faas_public = true
faas_package_type = "image"
"""
    w.medops_app.save()
    w.workload.kind = "faas"
    w.workload.save()
    with authority.current(w.ref):
        selected = source.target(w.domain, w.env.guid, w.env.version, w.workload.guid, reference=w.ref)
        w.snapshot = source.snapshot(w.domain, selected, w.domain.zone)
    w.snapshot.update(
        edge={
            "account_id": "123456789012",
            "region": "us-east-1",
            "dns_name": None,
            "canonical_zone_id": None,
            "certificate_status": "ISSUED",
            "allocation_state": "PENDING_NATIVE_DOMAIN",
        },
        native_api={"source_fingerprint": "a" * 64},
        writer="ROUTE53_ALIAS",
        dns_present=False,
        dns_matches=False,
        can_prepare=False,
        can_attach=True,
    )
    return w


def test_faas_original_service_pins_and_pending_alias_do_not_guess_target(admitted_faas):
    w = admitted_faas
    row = store.reserve(
        snapshot=w.snapshot,
        review_sha256=source.digest(w.snapshot),
        reference=w.ref,
        request_id=uuid4(),
        purpose="connect",
    )
    assert row.faas_service_id and row.api_service_id
    assert row.target_snapshot["target_kind"] == "FAAS_HTTP_API"
    assert row.custom_domain.required_dns_records == []
    assert row.custom_domain.edge_auth_enabled is False
    assert row.faas_service.provider_placement_identity is None
    assert row.api_service.provider_placement_identity is None
    with pytest.raises(DatabaseError), transaction.atomic():
        ManagedDomainAppBinding.all_objects.filter(pk=row.pk).update(api_service=None)
    from astrolift_services.models import ManagedService

    ManagedService.objects.filter(pk=row.api_service_id).update(config={"lambda_function_arn": "different"})
    with pytest.raises(source.DomainAppRouteError, match="CURRENT_FAAS_SERVICE"):
        store.stage_sent(row.guid, "api-domain", {"DomainName": row.hostname})


def test_faas_ack_allocation_published_only_from_retained_original_receipt(admitted_faas):
    from dataclasses import asdict, replace

    from _sdk.domain_route_http_api import HttpApiDomainReceipt

    w = admitted_faas
    row = store.reserve(
        snapshot=w.snapshot,
        review_sha256=source.digest(w.snapshot),
        reference=w.ref,
        request_id=uuid4(),
        purpose="connect",
    )
    receipt = HttpApiDomainReceipt(
        row.hostname,
        f"arn:aws:apigateway:us-east-1::/domainnames/{row.hostname}",
        "d-original.execute-api.us-east-1.amazonaws.com",
        "ZREGIONAL",
        "original-certificate",
        "AVAILABLE",
    )
    with pytest.raises(store.DomainRouteStoreError, match="ACK_REQUIRED"):
        store.publish_api_alias(row.guid, receipt)
    intent = store.stage_sent(row.guid, "api-domain", {"DomainName": row.hostname})
    store.retain_ack(row.guid, "api-domain", intent, asdict(receipt))
    store.publish_api_alias(row.guid, receipt)
    row.custom_domain.refresh_from_db()
    assert row.custom_domain.required_dns_records == [
        {
            "kind": "A_ALIAS",
            "name": row.hostname,
            "value": receipt.regional_target,
            "target_zone_id": receipt.hosted_zone_id,
        }
    ]
    with pytest.raises(store.DomainRouteStoreError, match="ACK_REQUIRED"):
        store.publish_api_alias(
            row.guid, replace(receipt, regional_target="d-other.execute-api.us-east-1.amazonaws.com")
        )
    store.stage_sent(row.guid, "api-mapping", {"ApiId": "abc123"})


def test_bound_custom_domain_changes_refuse_and_legacy_workers_never_run(admitted_faas):
    from astrolift_workflows.activities.custom_domain import (
        _ensure_records_sync,
        _issue_cert_sync,
        _probe_sync,
        _transition_sync,
    )

    w = admitted_faas
    row = store.reserve(
        snapshot=w.snapshot,
        review_sha256=source.digest(w.snapshot),
        reference=w.ref,
        request_id=uuid4(),
        purpose="connect",
    )
    for call in (
        lambda: _ensure_records_sync(row.custom_domain_id),
        lambda: _issue_cert_sync(row.custom_domain_id),
        lambda: _probe_sync(row.custom_domain_id),
        lambda: _transition_sync(row.custom_domain_id, True),
    ):
        with pytest.raises(source.DomainAppRouteError, match="PROTECTED_DOMAIN_ROUTE_MANAGE"):
            call()
    type(row.custom_domain).all_objects.filter(pk=row.custom_domain_id).update(edge_auth_enabled=True)
    with pytest.raises(store.DomainRouteStoreError, match="ORIGINAL_CUSTOM_DOMAIN_CHANGED"):
        store.stage_sent(row.guid, "api-domain", {"DomainName": row.hostname})


def test_bound_claim_survives_generic_sync_release_and_no_legacy_teardown(admitted_faas, monkeypatch):
    from astrolift_registry.hostname_claims import release_app_hostname_claims, sync_workload_hostname_claims
    from astrolift_registry.models import HostnameClaim
    from astrolift_workflows.activities.app_teardown import (
        _delete_app_namespaces_sync,
        _mark_tearing_down_sync,
    )
    from astrolift_workflows.activities.managed_service_lifecycle import _deprovision_sync

    w = admitted_faas
    row = store.reserve(
        snapshot=w.snapshot,
        review_sha256=source.digest(w.snapshot),
        reference=w.ref,
        request_id=uuid4(),
        purpose="connect",
    )
    original = HostnameClaim.objects.get(managed_domain=w.domain, hostname=row.hostname)
    sync_workload_hostname_claims(w.medops_app)
    assert release_app_hostname_claims(w.medops_app) == 0
    original.refresh_from_db()
    assert original.deleted_at is None and original.hostname == row.hostname
    for call in (
        lambda: _mark_tearing_down_sync(w.medops_app.pk),
        lambda: _delete_app_namespaces_sync(w.medops_app.pk),
        lambda: _deprovision_sync(row.api_service_id, False, False),
    ):
        with pytest.raises(source.DomainAppRouteError, match="PROTECTED_DOMAIN_ROUTE_MANAGE"):
            call()
    w.medops_app.refresh_from_db()
    assert w.medops_app.provisioning_status != "tearing_down"


def test_new_eks_target_refused_without_changing_ordinary_domain_behavior(admitted):
    w = admitted
    w.workload.kind = "deployment"
    w.workload.save()
    with (
        authority.current(w.ref),
        pytest.raises(source.DomainAppRouteError, match="DEPLOYMENT_DOMAIN_ROUTE_NOT_SUPPORTED"),
    ):
        source.target(w.domain, w.env.guid, w.env.version, w.workload.guid, reference=w.ref)
    from astrolift_lifecycle.models import CustomDomain

    legacy = CustomDomain.objects.create(registered_app=w.medops_app, hostname="legacy.example.test")
    assert source.legacy_custom_domain_allowed(legacy)


def test_bound_zone_legacy_provision_reissue_delete_stop_before_client(admitted_faas, monkeypatch):
    from astrolift_workflows.activities import deprovision_managed_domain as delete
    from astrolift_workflows.activities import provision_managed_domain as provision

    w = admitted_faas
    store.reserve(
        snapshot=w.snapshot,
        review_sha256=source.digest(w.snapshot),
        reference=w.ref,
        request_id=uuid4(),
        purpose="connect",
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Legacy provider must never be constructed for an owned route")

    monkeypatch.setattr(provision, "_get_cluster_and_dns_driver", forbidden)
    monkeypatch.setattr(delete, "_get_cluster_and_dns_driver", forbidden)
    for call in (
        lambda: provision._provision_dns_zone_sync(w.cluster.pk, w.domain.zone),
        lambda: provision._request_wildcard_cert_sync(w.cluster.pk, w.domain.zone, "ZOWNED"),
        lambda: delete._deprovision_sync(w.cluster.pk, w.domain.zone),
    ):
        with pytest.raises(source.DomainAppRouteError, match="PROTECTED_DOMAIN_ROUTE_MANAGE"):
            call()


def test_actual_protected_native_fk_lock_refuses_before_stage_reservation(admitted_faas):
    import time

    w = admitted_faas
    row = store.reserve(
        snapshot=w.snapshot,
        review_sha256=source.digest(w.snapshot),
        reference=w.ref,
        request_id=uuid4(),
        purpose="connect",
    )
    separate = connection.Database.connect(**connection.get_connection_params())
    try:
        with separate.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM astrolift_services_managedservice WHERE id=%s FOR UPDATE",
                [row.api_service_id],
            )
        start = time.monotonic()
        with pytest.raises(DatabaseError):
            store.stage_sent(row.guid, "api-domain", {"DomainName": row.hostname})
        assert time.monotonic() - start < 2
        row.refresh_from_db()
        assert "api-domain" not in row.native_receipts
    finally:
        separate.rollback()
        separate.close()
