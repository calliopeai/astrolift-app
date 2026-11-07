"""Tenant-scoped NVKE connection configuration; credentials are write-only."""

from __future__ import annotations

import json
import re
from uuid import UUID

from django.contrib.auth import get_user_model
from django.db import transaction
from nvke.client import Endpoint
from nvke.contract import upstream_revision

from astrolift_clusters.models import NvkeConnection, NvkeOperation, NvkeSite
from astrolift_clusters.scopes import _org_scope
from astrolift_identity.api_tokens import session_may_act_in
from astrolift_identity.models import Organization
from core.permissions import Permission, PermissionDenied, check_permission
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest
from core.tenancy import get_current_tenant

_NAMESPACE = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?\Z")
_SLUG = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,198}[a-z0-9])?\Z")
_CREDENTIAL_FIELDS = {
    "token": 16384,
    "ca_pem": 262144,
    "client_certificate_pem": 262144,
    "client_key_pem": 262144,
}


class NvkeSiteError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def authorized(permission):
    scope = _org_scope(permission)
    check_permission(permission, scope=scope)
    tenant = get_current_tenant()
    org = Organization.objects.filter(pk=scope.id, deleted_at__isnull=True).first()
    actor = (
        get_user_model().objects.filter(pk=tenant.actor_user_id if tenant else None, is_active=True).first()
    )
    if org is None or actor is None or not session_may_act_in(actor, org.pk):
        raise PermissionDenied(permission, scope, "NVKE authority is unavailable")
    return org, actor


def site_row(site_id, org, *, locked=False):
    try:
        guid = UUID(str(site_id))
    except (ValueError, TypeError, AttributeError):
        raise NvkeSiteError("NOT_FOUND", "NVKE site not found") from None
    query = NvkeSite.objects
    if locked:
        query = query.select_for_update()
    row = query.filter(guid=guid, organization=org).first()
    if row is None:
        raise NvkeSiteError("NOT_FOUND", "NVKE site not found")
    return row


def _version(row, expected):
    if type(expected) is not int or expected != row.version:
        raise NvkeSiteError("VERSION_MISMATCH", "NVKE configuration changed; refresh before retrying")


def create_site(*, name, slug, organization_namespace):
    org, actor = authorized(Permission.NVKE_ADMIN)
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 200 or any(ord(c) < 32 for c in name):
        raise NvkeSiteError("INVALID_INPUT", "Use a site name of at most 200 characters")
    if not isinstance(slug, str) or _SLUG.fullmatch(slug) is None:
        raise NvkeSiteError("INVALID_INPUT", "Use a valid site slug")
    if not isinstance(organization_namespace, str) or _NAMESPACE.fullmatch(organization_namespace) is None:
        raise NvkeSiteError("INVALID_INPUT", "Use the site's organization namespace")
    with transaction.atomic():
        # Lock the org to serialize the live-slug check with another create.
        Organization.objects.select_for_update().get(pk=org.pk)
        if NvkeSite.objects.filter(organization=org, slug=slug).exists():
            raise NvkeSiteError("CONFLICT", "A live NVKE site already uses this slug")
        return NvkeSite.objects.create(
            organization=org,
            name=name.strip(),
            slug=slug,
            organization_namespace=organization_namespace,
            upstream_revision=upstream_revision(),
            created_by=actor,
            updated_by=actor,
        )


def list_sites():
    org, _actor = authorized(Permission.NVKE_READ)
    return NvkeSite.objects.filter(organization=org).order_by("name", "guid")


def read_site(site_id):
    org, _actor = authorized(Permission.NVKE_READ)
    return site_row(site_id, org)


def _credential_payload(payload):
    if not isinstance(payload, dict) or payload.keys() - _CREDENTIAL_FIELDS.keys():
        raise NvkeSiteError("INVALID_INPUT", "NVKE credentials contain unsupported fields")
    for key, value in payload.items():
        if not isinstance(value, str) or len(value) > _CREDENTIAL_FIELDS[key] or "\x00" in value:
            raise NvkeSiteError("INVALID_INPUT", "NVKE credential field is invalid")
    token = payload.get("token", "")
    if token and any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise NvkeSiteError("INVALID_INPUT", "NVKE bearer credential is invalid")
    if bool(payload.get("client_certificate_pem")) != bool(payload.get("client_key_pem")):
        raise NvkeSiteError("INVALID_INPUT", "Supply the client certificate and private key together")
    return json.dumps({key: value for key, value in payload.items() if value}, sort_keys=True).encode()


def put_connection(
    *,
    site_id,
    expected_site_version,
    surface,
    expected_connection_version,
    endpoint,
    credentials=None,
    allow_http=False,
    timeout_seconds=30,
):
    org, actor = authorized(Permission.NVKE_ADMIN)
    if surface not in NvkeConnection.Surface.values:
        raise NvkeSiteError("INVALID_INPUT", "Unknown NVKE connection surface")
    if type(allow_http) is not bool or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
        raise NvkeSiteError("INVALID_INPUT", "Use a connection timeout between 1 and 300 seconds")
    if not isinstance(endpoint, str) or len(endpoint) > 2048:
        raise NvkeSiteError("INVALID_INPUT", "Use an NVKE endpoint of at most 2048 characters")
    try:
        Endpoint(surface, endpoint, allow_http=allow_http)
    except (ValueError, TypeError):
        raise NvkeSiteError(
            "INVALID_INPUT", "Use a valid NVKE endpoint without embedded credentials"
        ) from None
    plaintext = _credential_payload(credentials) if credentials is not None else None
    sealed = encrypt_at_rest(plaintext) if plaintext is not None and plaintext != b"{}" else None
    with transaction.atomic():
        site = site_row(site_id, org, locked=True)
        _version(site, expected_site_version)
        row = NvkeConnection.objects.select_for_update().filter(site=site, surface=surface).first()
        if row is None:
            if type(expected_connection_version) is not int or expected_connection_version != 0:
                raise NvkeSiteError("VERSION_MISMATCH", "The NVKE connection no longer matches the request")
            row = NvkeConnection(site=site, surface=surface, created_by=actor)
        else:
            _version(row, expected_connection_version)
        row.endpoint = endpoint
        row.allow_http = allow_http
        row.timeout_seconds = timeout_seconds
        row.is_enabled = True
        row.verified_at = None
        row.verification_code = ""
        row.updated_by = actor
        if credentials is not None:
            row.credential_backend_kind = sealed.backend_kind if sealed else ""
            row.credential_ciphertext = sealed.backend_ref if sealed else b""
        row.save()
        site.updated_by = actor
        site.save(update_fields=["updated_by", "updated_at", "version"])
        return row


def credentials_for(connection):
    if not connection.credential_ciphertext:
        return {}
    try:
        plaintext = decrypt(
            EncryptedSecret(connection.credential_backend_kind, bytes(connection.credential_ciphertext))
        )
        payload = json.loads(plaintext)
        _credential_payload(payload)
    except Exception:
        raise NvkeSiteError("CREDENTIALS_UNAVAILABLE", "NVKE credentials could not be loaded") from None
    return payload


def set_site_enabled(*, site_id, expected_site_version, enabled):
    org, actor = authorized(Permission.NVKE_ADMIN)
    if type(enabled) is not bool:
        raise NvkeSiteError("INVALID_INPUT", "Use an explicit site enabled state")
    with transaction.atomic():
        row = site_row(site_id, org, locked=True)
        _version(row, expected_site_version)
        row.is_active = enabled
        row.updated_by = actor
        row.save(update_fields=["is_active", "updated_by", "updated_at", "version"])
        return row


def remove_site(*, site_id, expected_site_version):
    org, actor = authorized(Permission.NVKE_ADMIN)
    with transaction.atomic():
        row = site_row(site_id, org, locked=True)
        _version(row, expected_site_version)
        if (
            NvkeOperation.objects.filter(site=row)
            .exclude(status__in=["succeeded", "failed", "cancelled"])
            .exists()
        ):
            raise NvkeSiteError(
                "PRECONDITION", "Resolve outstanding NVKE operations before removing the site"
            )
        row.soft_delete(by=actor)
        return row
