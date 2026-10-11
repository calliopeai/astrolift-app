"""Explicit reviewed write workflow; reads never advance provider effects."""

from __future__ import annotations

import time
from dataclasses import asdict
from types import SimpleNamespace

from astrolift_lifecycle import domain_app_route as source
from astrolift_lifecycle import domain_app_route_store as store


def _row(binding_id):
    with store.locked(binding_id) as row:
        # Every caller receives committed metadata, never a lock across SDK I/O.
        return row


def _state(binding_id, status, reason, *, dns="UNKNOWN", ingress="UNKNOWN", tls="UNKNOWN"):
    row = store.progress(
        binding_id, status=status, reason=reason, states={"dns": dns, "ingress": ingress, "tls": tls}
    )
    return {"operation_id": str(row.guid), "status": row.status, "reason": row.reason}


def reconcile(binding_id, *, checkpoint):
    deadline = time.monotonic() + 90

    def current():
        checkpoint()
        if time.monotonic() >= deadline:
            raise source.DomainAppRouteError("ROUTE_ACTIVITY_DEADLINE")
        return _row(binding_id)

    current()
    row = current()
    from core.app_deploy import driver_for_capability

    if row.target_snapshot["target_kind"] != "FAAS_HTTP_API":
        raise source.DomainAppRouteError("DEPLOYMENT_DOMAIN_ROUTE_NOT_SUPPORTED")
    current()
    dns = driver_for_capability(row.tenant_cluster, "dns")
    current()
    accepted = row.target_snapshot

    def edge():
        fresh = current()
        domain = fresh.managed_domain
        if accepted["target_kind"] == "FAAS_HTTP_API":
            selected = store.current(fresh)
            with source.http_api_port(
                selected,
                fresh.hostname,
                operation_id=fresh.guid,
                current=current,
                certificate_arn=domain.provision_cert_id,
                source_fingerprint=accepted["native_api"]["source_fingerprint"],
            ) as (port, target):
                port.observe_target(target, current=current)
            metadata = dict(accepted["edge"])
            if domain.provision_cert_id:
                cert = dns.observe_reviewed_domain_certificate(
                    zone=accepted["zone"],
                    hostname=fresh.hostname,
                    arn=domain.provision_cert_id,
                    expected_account_id=metadata["account_id"],
                    checkpoint=current,
                )
                metadata["certificate_status"] = cert.status
            else:
                metadata["certificate_status"] = "MISSING"
            return SimpleNamespace(**metadata, certificate_arn=domain.provision_cert_id)
        raise source.DomainAppRouteError("DEPLOYMENT_DOMAIN_ROUTE_NOT_SUPPORTED")

    def effect(name, payload, send):
        fresh = current()
        prior = fresh.native_receipts.get(name)
        if prior is not None:
            if prior["phase"] in ("SENT", "UNKNOWN"):
                return None
            return prior
        edge()
        intent = store.stage_sent(binding_id, name, payload)

        def admitted_send():
            fresh = current()
            stage = fresh.native_receipts.get(name)
            if (
                stage is None
                or any(stage.get(k) != intent[k] for k in ("nonce", "request_sha256"))
                or stage["phase"] not in ("SENT", "ACK")
            ):
                raise source.DomainAppRouteError("ORIGINAL_NATIVE_ROUTE_INTENT_CHANGED")

        try:
            send(admitted_send, lambda ack: store.retain_ack(binding_id, name, intent, ack))
            current()
        except Exception:
            # Retain any returned original ACK even when fresh admission fails.
            # An unknown send never returns to UNSENT and is never retried.
            store.unknown(binding_id, name, intent)
            raise
        return current().native_receipts[name]

    observed_edge = edge()
    if row.purpose == row.Purpose.PREPARE:
        certificate = effect(
            "certificate",
            {
                "zone": accepted["zone"],
                "hostname": row.hostname,
                "operation": str(row.guid),
                "account": observed_edge.account_id,
                "region": observed_edge.region,
            },
            lambda fresh, ack: dns.request_reviewed_domain_certificate(
                zone=accepted["zone"],
                hostname=row.hostname,
                operation_id=str(row.guid),
                expected_account_id=observed_edge.account_id,
                checkpoint=fresh,
                record_ack=lambda arn: ack({"certificate_arn": arn}),
            ),
        )
        if certificate is None:
            return _state(binding_id, "unknown", "ORIGINAL_CERTIFICATE_ACK_REQUIRED")
        arn = certificate["ack"]["certificate_arn"]
        cert = dns.observe_reviewed_domain_certificate(
            zone=accepted["zone"],
            hostname=row.hostname,
            arn=arn,
            expected_account_id=observed_edge.account_id,
            checkpoint=current,
        )
        if len(cert.validation_records) > 8:
            raise source.DomainAppRouteError("CERTIFICATE_VALIDATION_INVENTORY_LIMIT")
        store.publish_certificate(binding_id, arn, cert.validation_records)
        current()
        for name, value in cert.validation_records:
            args = {
                "zone_id": accepted["zone_id"],
                "zone": accepted["zone"],
                "name": name,
                "value": value,
                "expected_account_id": observed_edge.account_id,
            }
            observed = dns.observe_reviewed_certificate_validation(**args, checkpoint=current)
            stage_name = "validation-" + source.digest([name, value])[:24]
            prior = current().native_receipts.get(stage_name)
            if prior is not None and prior["phase"] in ("SENT", "UNKNOWN"):
                return _state(binding_id, "unknown", "ORIGINAL_VALIDATION_ACK_REQUIRED", tls=cert.status)
            if observed.present and not observed.matches:
                raise source.DomainAppRouteError("NATIVE_VALIDATION_RECORD_CONFLICT")
            if not observed.present:
                if prior is not None:
                    raise source.DomainAppRouteError("ORIGINAL_VALIDATION_RECORD_REMOVED")
                record = effect(
                    stage_name,
                    args,
                    lambda fresh, ack, args=args: dns.create_reviewed_certificate_validation(
                        **args,
                        checkpoint=fresh,
                        record_ack=lambda result: ack({"change_id": result.change_id}),
                    ),
                )
                if record is None:
                    return _state(binding_id, "unknown", "ORIGINAL_VALIDATION_ACK_REQUIRED", tls=cert.status)
        current()
        if cert.status == "ISSUED":
            return _state(binding_id, "ready", "DOMAIN_CERTIFICATE_PREPARED", tls="ISSUED")
        if cert.status != "PENDING_VALIDATION":
            return _state(binding_id, "refused", "NATIVE_CERTIFICATE_UNAVAILABLE", tls=cert.status)
        return _state(binding_id, "pending", "CERTIFICATE_VALIDATION_PENDING", tls="PENDING_VALIDATION")

    if observed_edge.certificate_status != "ISSUED":
        raise source.DomainAppRouteError("ISSUED_DOMAIN_CERTIFICATE_REQUIRED")
    store.current(current())
    if accepted["target_kind"] == "FAAS_HTTP_API":
        return _http_api_connect(binding_id, accepted=accepted, current=current, effect=effect, dns=dns)
    raise source.DomainAppRouteError("DEPLOYMENT_DOMAIN_ROUTE_NOT_SUPPORTED")


def _http_api_connect(binding_id, *, accepted, current, effect, dns):
    from _sdk.dns import ExactAliasRoute
    from _sdk.domain_route_http_api import (
        HttpApiDomainReceipt,
        HttpApiEffectHooks,
        HttpApiMappingReceipt,
        HttpApiSubmission,
    )

    def invoke(stage_name, kind, original_domain=None):
        row = current()
        prior = row.native_receipts.get(stage_name)
        if prior is not None:
            if prior["phase"] in ("SENT", "UNKNOWN"):
                return None
            receipt_type = HttpApiDomainReceipt if kind == "domain" else HttpApiMappingReceipt
            return receipt_type(**prior["ack"])
        intent = None

        def commit(payload):
            nonlocal intent
            current()
            intent = store.stage_sent(binding_id, stage_name, payload)
            return HttpApiSubmission(str(row.guid), intent["nonce"], intent["request_sha256"])

        def ack(submission, receipt):
            if (
                intent is None
                or submission.operation_id != str(row.guid)
                or submission.nonce != intent["nonce"]
                or submission.request_sha256 != intent["request_sha256"]
            ):
                raise source.DomainAppRouteError("ORIGINAL_API_SUBMISSION_CHANGED")
            store.retain_ack(binding_id, stage_name, intent, asdict(receipt))

        try:
            selected = store.current(current())
            with source.http_api_port(
                selected,
                row.hostname,
                operation_id=row.guid,
                current=current,
                certificate_arn=accepted["certificate_id"],
                source_fingerprint=accepted["native_api"]["source_fingerprint"],
            ) as (port, target):
                hooks = HttpApiEffectHooks(commit, ack)
                result = (
                    port.create_domain(target, hooks=hooks, current=current)
                    if kind == "domain"
                    else port.create_mapping(target, original_domain, hooks=hooks, current=current)
                )
            current()
            return result
        except Exception:
            if intent is not None:
                store.unknown(binding_id, stage_name, intent)
            raise

    domain = invoke("api-domain", "domain")
    if domain is None:
        return _state(binding_id, "unknown", "ORIGINAL_API_DOMAIN_ACK_REQUIRED")
    row = current()
    selected = store.current(row)
    with source.http_api_port(
        selected,
        row.hostname,
        operation_id=row.guid,
        current=current,
        certificate_arn=accepted["certificate_id"],
        source_fingerprint=accepted["native_api"]["source_fingerprint"],
    ) as (port, target):
        observed_domain = port.observe_domain(target, domain, current=current)
    store.publish_api_alias(binding_id, domain)
    if observed_domain.state != "AVAILABLE":
        return _state(binding_id, "pending", "API_CUSTOM_DOMAIN_PENDING", ingress="PENDING", tls="PENDING")
    mapping = invoke("api-mapping", "mapping", domain)
    if mapping is None:
        return _state(
            binding_id, "unknown", "ORIGINAL_API_MAPPING_ACK_REQUIRED", ingress="OBSERVED", tls="OBSERVED"
        )
    row = current()
    with source.http_api_port(
        store.current(row),
        row.hostname,
        operation_id=row.guid,
        current=current,
        certificate_arn=accepted["certificate_id"],
        source_fingerprint=accepted["native_api"]["source_fingerprint"],
    ) as (port, target):
        port.observe_mapping(target, domain, mapping, current=current)
    route = ExactAliasRoute(
        accepted["zone_id"],
        accepted["zone"],
        row.hostname,
        domain.regional_target,
        domain.hosted_zone_id,
        accepted["edge"]["account_id"],
        "HTTP_API",
    )
    receipt = effect(
        "alias",
        asdict(route),
        lambda fresh, ack: dns.create_exact_alias_route(
            route, checkpoint=fresh, record_ack=lambda result: ack({"change_id": result.change_id})
        ),
    )
    if receipt is None:
        return _state(
            binding_id, "unknown", "ORIGINAL_DNS_CHANGE_ACK_REQUIRED", ingress="OBSERVED", tls="OBSERVED"
        )
    change = dns.observe_reviewed_dns_change(
        receipt["ack"]["change_id"], expected_account_id=route.expected_account_id, checkpoint=current
    )
    observation = dns.observe_exact_alias_route(route, checkpoint=current)
    # Re-observe the original allocation and mapping after DNS waits. A
    # recorded ALIAS never authorizes a replacement API or native domain.
    row = current()
    with source.http_api_port(
        store.current(row),
        row.hostname,
        operation_id=row.guid,
        current=current,
        certificate_arn=accepted["certificate_id"],
        source_fingerprint=accepted["native_api"]["source_fingerprint"],
    ) as (port, target):
        final_domain = port.observe_domain(target, domain, current=current)
        port.observe_mapping(target, domain, mapping, current=current)
    if not observation.matches or change != "INSYNC" or final_domain.state != "AVAILABLE":
        return _state(
            binding_id,
            "pending",
            "NATIVE_API_DNS_RECONCILIATION_PENDING",
            dns="PENDING",
            ingress="OBSERVED",
            tls="OBSERVED",
        )
    current()
    return _state(
        binding_id,
        "ready",
        "NATIVE_API_ROUTE_CONFIGURATION_OBSERVED",
        dns="OBSERVED",
        ingress="OBSERVED",
        tls="OBSERVED",
    )
