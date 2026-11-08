"""Explicit metrics authority; no connection or credential fallback."""

import hashlib
from contextlib import contextmanager
from dataclasses import replace

from django.db import transaction
from django.utils import timezone
from nvke.client import validate_endpoint_url
from nvke.credential_metrics import CredentialMetricsClient

from astrolift_clusters import nvke_operations as outbox
from astrolift_clusters import nvke_sites as sites
from astrolift_clusters.models import NvkeMetricsAuthority
from astrolift_clusters.nvke_connections import connection_tls
from astrolift_identity import abac
from astrolift_identity.api_tokens import (
    get_current_api_token,
    reset_current_api_token,
    set_current_api_token,
    with_active_org_member,
)
from astrolift_identity.models import ApiToken
from core.permissions import Permission
from core.secrets import encrypt_at_rest


def actor(expected, *, require_read=False):
    org, caller = sites.authorized(Permission.NVKE_ADMIN)
    if type(expected) is not int or caller.pk != expected:
        raise sites.NvkeSiteError("SOURCE_CHANGED", "The reviewed NVKE metrics caller changed")
    token = get_current_api_token()
    if token is not None:
        token = with_active_org_member(
            ApiToken.objects.filter(guid=token.guid, user=caller, organization=org, is_revoked=False),
            user="user",
            organization="organization",
        ).first()
        if token is None or (token.expires_at is not None and token.expires_at <= timezone.now()):
            raise sites.NvkeSiteError("AUTHORITY_REVOKED", "The NVKE metrics caller is no longer authorized")
    mark = set_current_api_token(token)
    try:
        with abac.request_attributes(replace(abac.attributes_for(caller.pk), cache={})):
            sites.authorized(Permission.NVKE_ADMIN)
            if require_read:
                sites.authorized(Permission.NVKE_READ)
    finally:
        reset_current_api_token(mark)
    return org, caller


def read(site_id):
    org, _caller = sites.authorized(Permission.NVKE_READ)
    site = sites.site_row(site_id, org)
    return NvkeMetricsAuthority.objects.select_related("site").filter(site=site).first()


def configure(
    *,
    site_id,
    expected_actor_id,
    expected_site_version,
    expected_version,
    endpoint,
    credentials=None,
    allow_http=False,
    timeout_seconds=30,
):
    org, caller = actor(expected_actor_id)
    if (
        type(allow_http) is not bool
        or type(timeout_seconds) is not int
        or not 1 <= timeout_seconds <= 300
        or not isinstance(endpoint, str)
        or not 1 <= len(endpoint) <= 2048
    ):
        raise sites.NvkeSiteError(
            "INVALID_INPUT", "Use an explicit bounded NVKE metrics endpoint and timeout"
        )
    try:
        validate_endpoint_url(endpoint, allow_http=allow_http)
    except (ValueError, TypeError):
        raise sites.NvkeSiteError(
            "INVALID_INPUT", "Use an NVKE metrics URL without embedded credentials"
        ) from None
    plaintext = sites._credential_payload(credentials) if credentials is not None else None
    with transaction.atomic():
        site = sites.site_row(site_id, org, locked=True)
        sites._version(site, expected_site_version)
        row = NvkeMetricsAuthority.objects.select_for_update().filter(site=site).first()
        if row is None:
            if type(expected_version) is not int or expected_version != 0:
                raise sites.NvkeSiteError("VERSION_MISMATCH", "NVKE metrics authority changed")
            row = NvkeMetricsAuthority(site=site, created_by=caller)
        else:
            sites._version(row, expected_version)
        if plaintext is not None:
            sealed = encrypt_at_rest(plaintext) if plaintext != b"{}" else None
            row.credential_backend_kind = sealed.backend_kind if sealed else ""
            row.credential_ciphertext = sealed.backend_ref if sealed else b""
        row.endpoint, row.allow_http, row.timeout_seconds = endpoint, allow_http, timeout_seconds
        row.is_enabled, row.verified_at, row.verification_code = True, None, ""
        row.updated_by = caller
        row.save()
        site.updated_by = caller
        site.save(update_fields=["updated_by", "updated_at", "version"])
        return row


def set_enabled(
    *, site_id, expected_actor_id, expected_site_version, expected_version, enabled=None, remove=False
):
    org, caller = actor(expected_actor_id)
    with transaction.atomic():
        site = sites.site_row(site_id, org, locked=True)
        sites._version(site, expected_site_version)
        row = NvkeMetricsAuthority.objects.select_for_update().filter(site=site).first()
        if row is None:
            raise sites.NvkeSiteError("NOT_FOUND", "NVKE metrics authority not found")
        sites._version(row, expected_version)
        if remove:
            row.deleted_at, row.deleted_by = timezone.now(), caller
            row.is_enabled = False
        else:
            if type(enabled) is not bool:
                raise sites.NvkeSiteError("INVALID_INPUT", "Use an explicit metrics enabled state")
            row.is_enabled = enabled
        row.verified_at, row.verification_code, row.updated_by = None, "", caller
        row.save()
        site.updated_by = caller
        site.save(update_fields=["updated_by", "updated_at", "version"])
        return row


def digest(row):
    return hashlib.sha256(
        outbox.canonical(
            {
                "id": str(row.guid),
                "site": str(row.site.guid),
                "version": row.version,
                "endpoint": row.endpoint,
                "enabled": row.is_enabled,
                "allow_http": row.allow_http,
                "timeout": row.timeout_seconds,
                "backend": row.credential_backend_kind,
                "ciphertext": hashlib.sha256(bytes(row.credential_ciphertext)).hexdigest(),
            }
        )
    ).hexdigest()


@contextmanager
def open_metrics(row, checkpoint):
    checkpoint()
    credentials = sites.credentials_for(row)
    with connection_tls(credentials) as tls:
        checkpoint()
        with CredentialMetricsClient(
            row.endpoint,
            allow_http=row.allow_http,
            verify=tls,
            timeout=row.timeout_seconds,
            token=(lambda: sites.credentials_for(row)["token"]) if credentials.get("token") else None,
            before_request=checkpoint,
        ) as client:
            yield client


def verify(*, site_id, expected_actor_id, expected_site_version, expected_version):
    org, _caller = actor(expected_actor_id)
    site = sites.site_row(site_id, org)
    sites._version(site, expected_site_version)
    row = NvkeMetricsAuthority.objects.select_related("site").filter(site=site).first()
    if row is None:
        raise sites.NvkeSiteError("NOT_FOUND", "NVKE metrics authority not found")
    sites._version(row, expected_version)
    original = digest(row)

    def checkpoint():
        current_org, _ = actor(expected_actor_id)
        current_site = sites.site_row(site_id, current_org)
        current = (
            NvkeMetricsAuthority.objects.select_related("site")
            .filter(pk=row.pk, site=current_site, is_enabled=True)
            .first()
        )
        if (
            not current_site.is_active
            or current_site.version != expected_site_version
            or current is None
            or digest(current) != original
        ):
            raise sites.NvkeSiteError("SOURCE_CHANGED", "NVKE metrics authority changed before verification")

    with open_metrics(row, checkpoint) as client:
        client.read()
    with transaction.atomic():
        sites.site_row(site_id, org, locked=True)
        checkpoint()
        updated = NvkeMetricsAuthority.objects.filter(pk=row.pk, version=row.version, is_enabled=True).update(
            verified_at=timezone.now(), verification_code="METRICS_READ"
        )
        if updated != 1:
            raise sites.NvkeSiteError("SOURCE_CHANGED", "NVKE metrics authority changed before verification")
    row.refresh_from_db()
    row.site.refresh_from_db()
    return row
