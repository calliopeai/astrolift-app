"""Short committed route reservations, immutable effect evidence and no blind resend."""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from uuid import uuid4

from django.db import connection, transaction

from astrolift_clusters.models import ManagedDomain, ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle import domain_app_route as source
from astrolift_lifecycle import domain_route_authority as authority
from astrolift_lifecycle.models import AppEnvironment, CustomDomain, Deployment, ManagedDomainAppBinding
from astrolift_registry.models import RegisteredApp, Workload
from core.tenancy import TenantContext, tenant_context


class DomainRouteStoreError(ValueError):
    pass


def standalone():
    if connection.in_atomic_block or not connection.get_autocommit():
        raise DomainRouteStoreError("COMMITTED_ROUTE_TRANSACTION_REQUIRED")


def _base_snapshot(row):
    return {
        key: value
        for key, value in row.target_snapshot.items()
        if key
        not in (
            "edge",
            "backend",
            "native_api",
            "writer",
            "dns_present",
            "dns_matches",
            "can_attach",
            "can_prepare",
        )
    }


def custom_snapshot(custom):
    if custom.deleted_at is not None:
        raise DomainRouteStoreError("ORIGINAL_CUSTOM_DOMAIN_RETIRED")
    return source.private_digest(
        {
            name: getattr(custom, name)
            for name in (
                "registered_app_id",
                "hostname",
                "is_active",
                "deleted_at",
                "validation_method",
                "validation_status",
                "certificate_id",
                "certificate_state",
                "edge_auth_enabled",
                "is_platform_managed_zone",
                "required_dns_records",
                "is_wildcard",
                "sni_cert_ref",
                "byo_certificate_pem",
            )
        }
    )


def _custom_current(row):
    if row.custom_domain_id is None:
        return
    custom = CustomDomain.all_objects.get(pk=row.custom_domain_id)
    expected = row.native_receipts.get("custom_snapshot")
    if (
        not isinstance(expected, str)
        or custom_snapshot(custom) != expected
        or custom.redirect_rules.exists()
        or custom.path_routes.exists()
    ):
        raise DomainRouteStoreError("ORIGINAL_CUSTOM_DOMAIN_CHANGED")


def _save_custom_snapshot(row, custom):
    receipts = copy.deepcopy(row.native_receipts)
    receipts["custom_snapshot"] = custom_snapshot(custom)
    row.native_receipts = receipts
    row.save(update_fields=["native_receipts", "version", "updated_at"])


def current(row):
    _custom_current(row)
    accepted = row.target_snapshot
    with authority.current(row.authority_reference):
        expected_domain_version = row.native_receipts.get("domain_update", {}).get(
            "version", accepted["domain_version"]
        )
        domain = source.selected_domain(accepted["domain_id"], expected_domain_version)
        selected = source.target(
            domain,
            accepted["environment_id"],
            accepted["environment_version"],
            accepted["workload_id"],
            reference=row.authority_reference,
        )
        actual = source.snapshot(domain, selected, row.hostname)
        expected = _base_snapshot(row)
        update = row.native_receipts.get("domain_update")
        if update:
            expected.update(
                domain_version=update["version"],
                certificate_id=update["certificate_id"],
                domain_config_sha=update["config_sha"],
            )
        if (
            actual != expected
            or selected[2].pk != row.deployment_id
            or selected[0].pk != row.app_environment_id
            or selected[1].pk != row.workload_id
        ):
            raise DomainRouteStoreError("CURRENT_ROUTE_TARGET_CHANGED")
    return selected


@contextmanager
def locked(binding_id, *, fresh=True):
    standalone()
    observed = (
        ManagedDomainAppBinding.all_objects.select_related("registered_app").filter(guid=binding_id).first()
    )
    if observed is None or observed.deleted_at is not None:
        raise DomainRouteStoreError("ORIGINAL_ROUTE_NOT_FOUND")
    app = observed.registered_app
    with transaction.atomic(durable=True):
        # Existing cluster/app writers acquire parents in this order. NOWAIT
        # also applies to every insert's FK parent before reserving effects.
        Organization.all_objects.select_for_update(nowait=True).get(pk=observed.organization_id)
        TenantCluster.all_objects.select_for_update(nowait=True).get(pk=observed.tenant_cluster_id)
        ProviderPlugin.all_objects.select_for_update(nowait=True).get(pk=observed.provider_plugin_id)
        if fresh and app.team_id:
            Team.all_objects.select_for_update(nowait=True).get(pk=app.team_id)
        if fresh and app.project_id:
            Project.all_objects.select_for_update(nowait=True).get(pk=app.project_id)
        RegisteredApp.all_objects.select_for_update(nowait=True).get(pk=app.pk)
        AppEnvironment.all_objects.select_for_update(nowait=True).get(pk=observed.app_environment_id)
        Workload.all_objects.select_for_update(nowait=True).get(pk=observed.workload_id)
        Deployment.all_objects.select_for_update(nowait=True).get(pk=observed.deployment_id)
        from astrolift_services.models import ManagedService

        for service_id in (observed.faas_service_id, observed.api_service_id):
            if service_id is not None:
                ManagedService.all_objects.select_for_update(nowait=True).get(pk=service_id)
        ManagedDomain.all_objects.select_for_update(nowait=True).get(pk=observed.managed_domain_id)
        if observed.custom_domain_id:
            CustomDomain.all_objects.select_for_update(nowait=True).get(pk=observed.custom_domain_id)
        row = ManagedDomainAppBinding.all_objects.select_for_update(nowait=True).get(pk=observed.pk)
        if row.deleted_at is not None or any(
            getattr(row, f) != getattr(observed, f) for f in row.ORIGINAL_FIELDS
        ):
            raise DomainRouteStoreError("ORIGINAL_ROUTE_CHANGED")
        with tenant_context(
            TenantContext(
                organization_id=row.organization_id, actor_user_id=row.authority_reference["payload"]["actor"]
            )
        ):
            if fresh:
                current(row)
            yield row


def stage_sent(binding_id, name, payload):
    if not name or len(name) > 128 or not isinstance(payload, dict) or len(json.dumps(payload)) > 16384:
        raise DomainRouteStoreError("INVALID_NATIVE_ROUTE_INTENT")
    with locked(binding_id) as row:
        stages = copy.deepcopy(row.native_receipts)
        if name in stages:
            raise DomainRouteStoreError("ORIGINAL_NATIVE_SEND_ALREADY_RESERVED")
        intent = {"phase": "SENT", "nonce": str(uuid4()), "request_sha256": source.digest(payload)}
        stages[name] = intent
        row.native_receipts = stages
        row.status = row.Status.CONFIGURING
        row.reason = "NATIVE_ROUTE_SEND_RESERVED"
        row.save(update_fields=["native_receipts", "status", "reason", "version", "updated_at"])
        return copy.deepcopy(intent)


_ACK_FIELDS = frozenset(
    (
        "hostname",
        "certificate_arn",
        "change_id",
        "uid",
        "resource_version",
        "kind",
        "namespace",
        "name",
        "request_sha256",
        "domain_name_arn",
        "regional_target",
        "hosted_zone_id",
        "certificate_arn",
        "state",
        "mapping_id",
        "api_id",
        "stage",
        "mapping_key",
    )
)


def retain_ack(binding_id, name, intent, acknowledgement):
    if (
        not isinstance(acknowledgement, dict)
        or not acknowledgement
        or set(acknowledgement) - _ACK_FIELDS
        or any(
            not isinstance(v, str) or (not v and k != "mapping_key") or len(v) > 512
            for k, v in acknowledgement.items()
        )
    ):
        raise DomainRouteStoreError("INVALID_NATIVE_ROUTE_ACK")
    with locked(binding_id, fresh=False) as row:
        stages = copy.deepcopy(row.native_receipts)
        original = stages.get(name)
        if (
            original is None
            or any(original.get(k) != intent.get(k) for k in ("nonce", "request_sha256"))
            or original["phase"] not in ("SENT", "UNKNOWN", "ACK", "OBSERVED")
        ):
            raise DomainRouteStoreError("ORIGINAL_NATIVE_ROUTE_INTENT_CHANGED")
        if original.get("ack") and original["ack"] != acknowledgement:
            raise DomainRouteStoreError("ORIGINAL_NATIVE_ROUTE_ACK_CHANGED")
        if original.get("ack") == acknowledgement:
            return
        original.update(phase="ACK", ack=acknowledgement)
        row.native_receipts = stages
        row.reason = "NATIVE_ROUTE_ACK_RETAINED"
        row.save(update_fields=["native_receipts", "reason", "version", "updated_at"])


def unknown(binding_id, name, intent):
    with locked(binding_id, fresh=False) as row:
        stages = copy.deepcopy(row.native_receipts)
        stage = stages.get(name)
        if stage is None or any(stage.get(k) != intent.get(k) for k in ("nonce", "request_sha256")):
            raise DomainRouteStoreError("ORIGINAL_NATIVE_ROUTE_INTENT_CHANGED")
        if stage["phase"] == "SENT":
            stage["phase"] = "UNKNOWN"
        row.native_receipts = stages
        row.status = row.Status.UNKNOWN
        row.reason = "ORIGINAL_NATIVE_ROUTE_OUTCOME_UNKNOWN"
        row.save(update_fields=["native_receipts", "status", "reason", "version", "updated_at"])


def observed(binding_id, name, intent):
    with locked(binding_id) as row:
        stages = copy.deepcopy(row.native_receipts)
        stage = stages.get(name)
        if (
            stage is None
            or stage.get("nonce") != intent["nonce"]
            or stage.get("request_sha256") != intent["request_sha256"]
            or stage["phase"] != "ACK"
        ):
            raise DomainRouteStoreError("ORIGINAL_NATIVE_ROUTE_ACK_REQUIRED")
        stage["phase"] = "OBSERVED"
        row.native_receipts = stages
        row.save(update_fields=["native_receipts", "version", "updated_at"])


def publish_certificate(binding_id, arn, records):
    with locked(binding_id) as row:
        stage = row.native_receipts.get("certificate")
        if (
            row.purpose != row.Purpose.PREPARE
            or stage is None
            or stage["phase"] not in ("ACK", "OBSERVED")
            or stage.get("ack", {}).get("certificate_arn") != arn
        ):
            raise DomainRouteStoreError("ORIGINAL_CERTIFICATE_ACK_REQUIRED")
        domain = ManagedDomain.all_objects.get(pk=row.managed_domain_id)
        if domain.provision_cert_id not in ("", arn):
            raise DomainRouteStoreError("ORIGINAL_DOMAIN_CERTIFICATE_CHANGED")
        config = dict(domain.dns_config or {})
        config["provision_cluster_id"] = str(row.tenant_cluster.guid)
        validation = [{"name": name, "type": "CNAME", "value": value} for name, value in records]
        if (
            domain.dns_config == config
            and domain.provision_cert_id == arn
            and domain.provision_validation_records == validation
        ):
            return
        domain.dns_config = config
        domain.provision_cert_id = arn
        domain.provision_validation_records = validation
        domain.save(
            update_fields=[
                "dns_config",
                "provision_cert_id",
                "provision_validation_records",
                "version",
                "updated_at",
            ]
        )
        receipts = copy.deepcopy(row.native_receipts)
        receipts["domain_update"] = {
            "version": domain.version,
            "certificate_id": arn,
            "config_sha": source.private_digest(config),
        }
        row.native_receipts = receipts
        row.status = row.Status.PENDING
        row.reason = "CERTIFICATE_VALIDATION_PENDING"
        row.save(update_fields=["native_receipts", "status", "reason", "version", "updated_at"])


def reserve(*, snapshot, review_sha256, reference, request_id, purpose):
    """Commit the reviewed original target before enqueue or any provider send."""
    standalone()
    request_id = source._guid(request_id)
    if purpose not in ManagedDomainAppBinding.Purpose.values or source.digest(snapshot) != review_sha256:
        raise DomainRouteStoreError("INVALID_REVIEWED_ROUTE")
    with authority.current(reference):
        tenant = __import__("core.tenancy", fromlist=["get_current_tenant"]).get_current_tenant()
        domain = source.selected_domain(snapshot["domain_id"], snapshot["domain_version"])
        selected = source.target(
            domain,
            snapshot["environment_id"],
            snapshot["environment_version"],
            snapshot["workload_id"],
            reference=reference,
        )
        env, workload, deployment, *_ = selected
        if source.snapshot(domain, selected, snapshot["hostname"]) != {
            key: value
            for key, value in snapshot.items()
            if key
            not in (
                "edge",
                "backend",
                "native_api",
                "writer",
                "dns_present",
                "dns_matches",
                "can_attach",
                "can_prepare",
            )
        }:
            raise DomainRouteStoreError("CURRENT_ROUTE_TARGET_CHANGED")
        if (
            snapshot["writer"] != "ROUTE53_ALIAS"
            or not snapshot["can_prepare" if purpose == "prepare" else "can_attach"]
        ):
            raise DomainRouteStoreError("REVIEWED_ROUTE_ACTION_UNAVAILABLE")
        with transaction.atomic(durable=True):
            Organization.all_objects.select_for_update(nowait=True).get(pk=tenant.organization_id)
            cluster = TenantCluster.all_objects.select_for_update(nowait=True).get(pk=env.tenant_cluster_id)
            ProviderPlugin.all_objects.select_for_update(nowait=True).get(pk=cluster.provider_plugin_id)
            app = env.registered_app
            if app.team_id:
                Team.all_objects.select_for_update(nowait=True).get(pk=app.team_id)
            if app.project_id:
                Project.all_objects.select_for_update(nowait=True).get(pk=app.project_id)
            RegisteredApp.all_objects.select_for_update(nowait=True).get(pk=app.pk)
            AppEnvironment.all_objects.select_for_update(nowait=True).get(pk=env.pk)
            Workload.all_objects.select_for_update(nowait=True).get(pk=workload.pk)
            Deployment.all_objects.select_for_update(nowait=True).get(pk=deployment.pk)
            services = source.faas_services(env, workload) if workload.kind == "faas" else None
            if services:
                from astrolift_services.models import ManagedService

                for native in services:
                    ManagedService.all_objects.select_for_update(nowait=True).get(pk=native.pk)
            ManagedDomain.all_objects.select_for_update(nowait=True).get(pk=domain.pk)
            # Recheck *after* all waits, including original authenticated ceiling.
            selected = source.target(
                domain,
                snapshot["environment_id"],
                snapshot["environment_version"],
                snapshot["workload_id"],
                reference=reference,
            )
            domain.refresh_from_db()
            if source.snapshot(domain, selected, snapshot["hostname"]) != {
                key: value
                for key, value in snapshot.items()
                if key
                not in (
                    "edge",
                    "backend",
                    "native_api",
                    "writer",
                    "dns_present",
                    "dns_matches",
                    "can_attach",
                    "can_prepare",
                )
            }:
                raise DomainRouteStoreError("CURRENT_ROUTE_TARGET_CHANGED")
            existing = ManagedDomainAppBinding.all_objects.filter(
                organization_id=tenant.organization_id, request_id=request_id
            ).first()
            if existing:
                if (
                    existing.deleted_at is not None
                    or existing.review_sha256 != review_sha256
                    or existing.purpose != purpose
                    or existing.authority_reference != reference
                ):
                    raise DomainRouteStoreError("ORIGINAL_ROUTE_REQUEST_CHANGED")
                return existing
            if (
                ManagedDomainAppBinding.all_objects.filter(managed_domain=domain, purpose="prepare").exists()
                and purpose == "prepare"
            ):
                raise DomainRouteStoreError("ORIGINAL_DOMAIN_PREPARATION_REQUIRES_RECOVERY")
            from django.contrib.auth import get_user_model

            get_user_model().objects.select_for_update(nowait=True).get(pk=tenant.actor_user_id)
            custom = None
            if purpose == "connect":
                from astrolift_registry.models import HostnameClaim

                if (
                    HostnameClaim.objects.filter(
                        managed_domain=domain, hostname=snapshot["hostname"]
                    ).exists()
                    or CustomDomain.objects.filter(hostname=snapshot["hostname"]).exists()
                ):
                    raise DomainRouteStoreError("HOSTNAME_ALREADY_CLAIMED")
                from core.app_deploy import oidc_auth_for_cluster

                gated = workload.kind == "deployment" and oidc_auth_for_cluster(cluster) is not None
                custom = CustomDomain.objects.create(
                    registered_app=app,
                    hostname=snapshot["hostname"],
                    is_platform_managed_zone=True,
                    edge_auth_enabled=gated,
                    validation_method="dns_01",
                    certificate_id=snapshot["certificate_id"],
                    certificate_state="active",
                    validation_status="validating",
                    required_dns_records=(
                        [
                            {
                                "kind": "A_ALIAS",
                                "name": snapshot["hostname"],
                                "value": snapshot["edge"]["dns_name"],
                                "target_zone_id": snapshot["edge"]["canonical_zone_id"],
                            }
                        ]
                        if snapshot["edge"]["dns_name"] is not None
                        else []
                    ),
                )
                HostnameClaim.objects.create(
                    managed_domain=domain,
                    hostname=snapshot["hostname"],
                    organization_id=tenant.organization_id,
                    registered_app=app,
                    environment=env,
                    workload=workload,
                )
            row = ManagedDomainAppBinding(
                organization_id=tenant.organization_id,
                managed_domain=domain,
                custom_domain=custom,
                registered_app=app,
                provider_plugin=cluster.provider_plugin,
                app_environment=env,
                workload=workload,
                deployment=deployment,
                tenant_cluster=cluster,
                faas_service=services[0] if services else None,
                api_service=services[1] if services else None,
                request_id=request_id,
                hostname=snapshot["hostname"],
                review_sha256=review_sha256,
                target_snapshot=snapshot,
                authority_reference=reference,
                purpose=purpose,
                created_by_id=tenant.actor_user_id,
                native_receipts={"custom_snapshot": custom_snapshot(custom)} if custom is not None else {},
            )
            row.workflow_id = "ManagedDomainAppRouteWorkflow-" + str(row.guid)
            row.save()
            return row


def progress(binding_id, *, status, reason, states=None):
    if status not in ManagedDomainAppBinding.Status.values or not reason or len(reason) > 64:
        raise DomainRouteStoreError("INVALID_ROUTE_PROGRESS")
    with locked(binding_id) as row:
        receipts = copy.deepcopy(row.native_receipts)
        if states is not None:
            if set(states) != {"dns", "ingress", "tls"} or any(
                not isinstance(s, str) or len(s) > 32 for s in states.values()
            ):
                raise DomainRouteStoreError("INVALID_ROUTE_OBSERVATION")
            receipts["states"] = states
        row.native_receipts = receipts
        row.status = status
        row.reason = reason
        if status == row.Status.READY:
            from django.utils import timezone

            row.observed_at = timezone.now()
            if row.purpose == row.Purpose.CONNECT:
                custom = CustomDomain.all_objects.get(pk=row.custom_domain_id)
                if (
                    custom.registered_app_id != row.registered_app_id
                    or custom.hostname != row.hostname
                    or custom.deleted_at is not None
                ):
                    raise DomainRouteStoreError("ORIGINAL_CUSTOM_DOMAIN_CHANGED")
                custom.validation_status = "validated"
                custom.certificate_state = "active"
                custom.save(update_fields=["validation_status", "certificate_state", "version", "updated_at"])
                receipts["custom_snapshot"] = custom_snapshot(custom)
                row.native_receipts = receipts
        row.save(
            update_fields=["native_receipts", "status", "reason", "observed_at", "version", "updated_at"]
        )
        return row


def dispatched(binding_id, *, confirmed):
    with locked(binding_id) as row:
        if row.status == row.Status.ACCEPTED:
            row.reason = "WORKFLOW_DISPATCH_CONFIRMED" if confirmed else "WORKFLOW_DISPATCH_UNCONFIRMED"
            row.save(update_fields=["reason", "version", "updated_at"])


def publish_api_alias(binding_id, receipt):
    from dataclasses import asdict

    from _sdk.domain_route_http_api import HttpApiDomainReceipt

    if type(receipt) is not HttpApiDomainReceipt:
        raise DomainRouteStoreError("ORIGINAL_API_DOMAIN_ACK_REQUIRED")
    with locked(binding_id) as row:
        stage = row.native_receipts.get("api-domain")
        if (
            row.purpose != row.Purpose.CONNECT
            or row.target_snapshot["target_kind"] != "FAAS_HTTP_API"
            or stage is None
            or stage["phase"] not in ("ACK", "OBSERVED")
            or stage.get("ack") != asdict(receipt)
        ):
            raise DomainRouteStoreError("ORIGINAL_API_DOMAIN_ACK_REQUIRED")
        custom = CustomDomain.all_objects.get(pk=row.custom_domain_id)
        records = [
            {
                "kind": "A_ALIAS",
                "name": row.hostname,
                "value": receipt.regional_target,
                "target_zone_id": receipt.hosted_zone_id,
            }
        ]
        if custom.required_dns_records not in ([], records):
            raise DomainRouteStoreError("ORIGINAL_API_ALIAS_TARGET_CHANGED")
        if custom.required_dns_records == records:
            return
        custom.required_dns_records = records
        custom.save(update_fields=["required_dns_records", "version", "updated_at"])
        _save_custom_snapshot(row, custom)


def failed(binding_id):
    """Failure metadata only: no fresh grant or ready/ownership transition."""
    with locked(binding_id, fresh=False) as row:
        unresolved = any(
            isinstance(value, dict) and value.get("phase") in ("SENT", "UNKNOWN")
            for value in row.native_receipts.values()
        )
        row.status = row.Status.UNKNOWN if unresolved else row.Status.REFUSED
        row.reason = "CURRENT_ROUTE_CONFIGURATION_UNAVAILABLE"
        row.save(update_fields=["status", "reason", "version", "updated_at"])
