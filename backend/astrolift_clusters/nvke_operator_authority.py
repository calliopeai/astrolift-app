"""Reviewed mTLS authority for a dedicated site's native operator agent."""

import hashlib
import hmac
import re
from contextlib import contextmanager
from urllib.parse import urlsplit

from django.db import transaction
from django.utils import timezone
from nvke.client import NvkeError, validate_endpoint_url
from nvke.contract import upstream_revision
from nvke.operator_agent_client import OperatorAgentClient
from nvke.operator_contract import ContractError, identifier

from astrolift_clusters import nvke_operations as outbox
from astrolift_clusters import nvke_sites as sites
from astrolift_clusters import nvke_temporal as native
from astrolift_clusters.models import NvkeOperatorAuthority
from astrolift_clusters.nvke_connections import connection_tls
from core.permissions import Permission
from core.secrets import encrypt_at_rest


def actor(expected):
    return native._actor(expected, require_read=True)


def read(site_id):
    org, _caller = sites.authorized(Permission.NVKE_READ)
    site = sites.site_row(site_id, org)
    return NvkeOperatorAuthority.objects.select_related("site").filter(site=site).first()


def digest(row):
    return hashlib.sha256(
        outbox.canonical(
            {
                "id": str(row.guid),
                "site": str(row.site.guid),
                "version": row.version,
                "endpoint": row.endpoint,
                "instance": str(row.instance_id),
                "bundle": row.bundle_manifest_sha256,
                "enabled": row.is_enabled,
                "timeout": row.timeout_seconds,
                "credential_backend": row.credential_backend_kind,
                "credential_sha256": hashlib.sha256(bytes(row.credential_ciphertext)).hexdigest(),
            }
        )
    ).hexdigest()


def _endpoint(value):
    try:
        if not isinstance(value, str) or not 1 <= len(value) <= 2048:
            raise ValueError
        validate_endpoint_url(value)
        if urlsplit(value).path not in {"", "/"}:
            raise ValueError
    except ValueError:
        raise NvkeError("INVALID_INPUT", "Use the operator agent's bounded HTTPS base endpoint") from None
    return value.rstrip("/")


def configure(
    *,
    site_id,
    expected_actor_id,
    expected_site_version,
    expected_version,
    endpoint,
    instance_id,
    bundle_manifest_sha256,
    timeout_seconds=30,
    credentials=None,
):
    org, caller = actor(expected_actor_id)
    endpoint = _endpoint(endpoint)
    try:
        instance_id = identifier(str(instance_id))
    except ContractError:
        raise NvkeError("INVALID_INPUT", "Use the reviewed canonical operator instance UUID") from None
    if (
        not isinstance(bundle_manifest_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", bundle_manifest_sha256)
        or type(timeout_seconds) is not int
        or not 1 <= timeout_seconds <= 300
    ):
        raise NvkeError(
            "INVALID_INPUT", "Review an explicit operator instance, bundle SHA-256 and bounded timeout"
        )
    with transaction.atomic():
        site = sites.site_row(site_id, org, locked=True)
        sites._version(site, expected_site_version)
        row = NvkeOperatorAuthority.objects.select_for_update().filter(site=site).first()
        if row is None:
            if type(expected_version) is not int or expected_version != 0:
                raise NvkeError("VERSION_MISMATCH", "Operator authority changed")
            row = NvkeOperatorAuthority(site=site, created_by=caller)
        else:
            sites._version(row, expected_version)
        if credentials is not None:
            if not isinstance(credentials, dict) or credentials.keys() - {
                "ca_pem",
                "client_certificate_pem",
                "client_key_pem",
            }:
                raise NvkeError("INVALID_INPUT", "Use an operator CA and paired mTLS client certificate/key")
            plaintext = sites._credential_payload(credentials)
            if not credentials.get("client_certificate_pem") or not credentials.get("client_key_pem"):
                raise NvkeError(
                    "INVALID_INPUT", "The operator agent requires a paired mTLS client certificate/key"
                )
            sealed = encrypt_at_rest(plaintext)
            row.credential_backend_kind, row.credential_ciphertext = sealed.backend_kind, sealed.backend_ref
        if not row.credential_ciphertext:
            raise NvkeError(
                "CREDENTIALS_UNAVAILABLE", "Configure explicit mTLS credentials for this operator agent"
            )
        row.endpoint, row.instance_id, row.bundle_manifest_sha256 = (
            endpoint,
            instance_id,
            bundle_manifest_sha256,
        )
        row.timeout_seconds, row.is_enabled, row.verified_at, row.verification_code = (
            timeout_seconds,
            True,
            None,
            "",
        )
        row.updated_by = caller
        row.save()
        site.updated_by = caller
        site.save(update_fields=["updated_by", "updated_at", "version"])
        row.site = site
        return row


def set_enabled(
    *, site_id, expected_actor_id, expected_site_version, expected_version, enabled=None, remove=False
):
    org, caller = actor(expected_actor_id)
    with transaction.atomic():
        site = sites.site_row(site_id, org, locked=True)
        sites._version(site, expected_site_version)
        row = NvkeOperatorAuthority.objects.select_for_update().filter(site=site).first()
        if row is None:
            raise NvkeError("NOT_FOUND", "Operator authority not found")
        sites._version(row, expected_version)
        if not remove and type(enabled) is not bool:
            raise NvkeError("INVALID_INPUT", "Use an explicit operator authority enabled state")
        row.is_enabled, row.verified_at, row.verification_code = False if remove else enabled, None, ""
        if remove:
            row.deleted_at, row.deleted_by = timezone.now(), caller
        row.updated_by = caller
        row.save()
        site.updated_by = caller
        site.save(update_fields=["updated_by", "updated_at", "version"])
        row.site = site
        return row


@contextmanager
def open_agent(row, *, checkpoint):
    checkpoint()
    credentials = sites.credentials_for(row)
    if not credentials.get("client_certificate_pem") or not credentials.get("client_key_pem"):
        raise NvkeError("CREDENTIALS_UNAVAILABLE", "Original operator mTLS authority is unavailable")
    with (
        connection_tls(credentials) as tls,
        OperatorAgentClient(
            row.endpoint,
            site_id=str(row.site.guid),
            instance_id=str(row.instance_id),
            bundle_manifest_sha256=row.bundle_manifest_sha256,
            timeout=row.timeout_seconds,
            verify=tls,
            before_request=checkpoint,
        ) as client,
    ):
        yield client


def verify(*, site_id, expected_actor_id, expected_site_version, expected_version):
    org, _caller = actor(expected_actor_id)
    site = sites.site_row(site_id, org)
    sites._version(site, expected_site_version)
    row = NvkeOperatorAuthority.objects.select_related("site").filter(site=site, is_enabled=True).first()
    if row is None:
        raise NvkeError("NOT_FOUND", "Enabled operator authority not found")
    sites._version(row, expected_version)
    original = digest(row)

    def checkpoint():
        current_org, _actor = actor(expected_actor_id)
        current_site = sites.site_row(site_id, current_org)
        current = (
            NvkeOperatorAuthority.objects.select_related("site")
            .filter(pk=row.pk, site=current_site, is_enabled=True)
            .first()
        )
        if (
            not current_site.is_active
            or current_site.version != expected_site_version
            or current is None
            or not hmac.compare_digest(digest(current), original)
        ):
            raise NvkeError("SOURCE_CHANGED", "Operator authority changed before verification")

    with open_agent(row, checkpoint=checkpoint) as client:
        client.identity()
    with transaction.atomic():
        sites.site_row(site_id, org, locked=True)
        checkpoint()
        changed = NvkeOperatorAuthority.objects.filter(
            pk=row.pk, version=row.version, is_enabled=True
        ).update(verified_at=timezone.now(), verification_code="AGENT_VERIFIED")
        if changed != 1:
            raise NvkeError("SOURCE_CHANGED", "Operator authority changed before verification")
        row.refresh_from_db()
    return row


def checkpoint(row):
    with outbox.original_actor(row):
        org, _actor = actor(row.actor_id)
        site = sites.site_row(row.site.guid, org)
        current = (
            NvkeOperatorAuthority.objects.select_related("site")
            .filter(
                site=site,
                guid=row.authority.get("operator_authority_id"),
                is_enabled=True,
                verified_at__isnull=False,
            )
            .first()
        )
        if (
            not site.is_active
            or site.version != row.expected_site_version
            or site.upstream_revision != upstream_revision()
            or site.upstream_revision != row.authority.get("revision")
            or site.organization_namespace != row.authority.get("organization_namespace")
            or current is None
            or not hmac.compare_digest(digest(current), row.authority.get("operator_authority_digest", ""))
        ):
            raise NvkeError("SOURCE_CHANGED", "Original operator execution authority changed")
        return current
