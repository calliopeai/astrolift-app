"""Explicit native workflow authority, independent of Astro's workflow service."""

import asyncio
import hashlib
import ipaddress
import json
from dataclasses import replace
from datetime import timedelta
from urllib.parse import urlsplit

from asgiref.sync import async_to_sync, sync_to_async
from django.db import transaction
from django.utils import timezone
from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest
from temporalio.client import Client
from temporalio.service import TLSConfig

from astrolift_clusters import nvke_sites as sites
from astrolift_clusters.models import NvkeTemporalAuthority
from astrolift_identity import abac
from astrolift_identity.api_tokens import (
    get_current_api_token,
    reset_current_api_token,
    set_current_api_token,
    with_active_org_member,
)
from astrolift_identity.models import ApiToken
from core.permissions import Permission
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest

_CREDENTIALS = {
    "api_key": 16384,
    "ca_pem": 262144,
    "client_certificate_pem": 262144,
    "client_key_pem": 262144,
}


def _invalid():
    return sites.NvkeSiteError(
        "INVALID_INPUT", "Use explicit native Temporal address, namespace, queue and valid TLS credentials"
    )


def _host(value):
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        raise _invalid()
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        try:
            host = value.encode("idna").decode("ascii").lower()
        except UnicodeError:
            raise _invalid() from None
        labels = host.rstrip(".").split(".")
        if len(host) > 253 or any(
            not label
            or len(label) > 63
            or not label[0].isalnum()
            or not label[-1].isalnum()
            or any(not (c.isalnum() or c == "-") for c in label)
            for label in labels
        ):
            raise _invalid() from None
        return host


def _address(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 512 or any(ord(c) < 33 for c in value):
        raise _invalid()
    try:
        parsed = urlsplit("//" + value)
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.port is None
            or not 1 <= parsed.port <= 65535
        ):
            raise _invalid()
        host = _host(parsed.hostname)
        return f"[{host}]:{parsed.port}" if ":" in host else f"{host}:{parsed.port}"
    except (ValueError, TypeError):
        raise _invalid() from None


def _name(value):
    try:
        if (
            isinstance(value, str)
            and value.strip() == value
            and value
            and value.isprintable()
            and len(value.encode("utf-8")) <= 255
        ):
            return value
    except UnicodeError:
        pass
    raise _invalid()


def _bundle(value):
    if not isinstance(value, dict) or value.keys() - _CREDENTIALS.keys():
        raise _invalid()
    for name, text in value.items():
        if not isinstance(text, str) or len(text) > _CREDENTIALS[name] or "\x00" in text:
            raise _invalid()
        try:
            if len(text.encode("utf-8")) > _CREDENTIALS[name]:
                raise _invalid()
        except UnicodeError:
            raise _invalid() from None
    if any(ord(c) < 33 or ord(c) > 126 for c in value.get("api_key", "")):
        raise _invalid()
    if bool(value.get("client_certificate_pem")) != bool(value.get("client_key_pem")):
        raise _invalid()
    try:
        return json.dumps({k: v for k, v in value.items() if v}, sort_keys=True).encode("utf-8")
    except UnicodeError:
        raise _invalid() from None


def credentials_for(row):
    if not row.credential_ciphertext:
        return {}
    try:
        value = json.loads(
            decrypt(EncryptedSecret(row.credential_backend_kind, bytes(row.credential_ciphertext)))
        )
        _bundle(value)
        return value
    except Exception:
        raise sites.NvkeSiteError(
            "CREDENTIALS_UNAVAILABLE", "Native Temporal credentials could not be loaded"
        ) from None


def _actor(expected, *, require_read=False):
    org, actor = sites.authorized(Permission.NVKE_ADMIN)
    if type(expected) is not int or actor.pk != expected:
        raise sites.NvkeSiteError("SOURCE_CHANGED", "The reviewed native Temporal caller changed")
    token = get_current_api_token()
    if token is not None:
        token = with_active_org_member(
            ApiToken.objects.filter(guid=token.guid, user=actor, organization=org, is_revoked=False),
            user="user",
            organization="organization",
        ).first()
        if token is None or (token.expires_at is not None and token.expires_at <= timezone.now()):
            raise sites.NvkeSiteError(
                "AUTHORITY_REVOKED", "The native Temporal caller is no longer authorized"
            )
    mark = set_current_api_token(token)
    try:
        with abac.request_attributes(replace(abac.attributes_for(actor.pk), cache={})):
            org, actor = sites.authorized(Permission.NVKE_ADMIN)
            if require_read:
                sites.authorized(Permission.NVKE_READ)
    finally:
        reset_current_api_token(mark)
    return org, actor


def read(site_id):
    org, _actor = sites.authorized(Permission.NVKE_READ)
    site = sites.site_row(site_id, org)
    return NvkeTemporalAuthority.objects.select_related("site").filter(site=site).first()


def configure(
    *,
    site_id,
    expected_actor_id,
    expected_site_version,
    expected_version,
    address,
    namespace,
    task_queue,
    server_name="",
    use_tls=True,
    timeout_seconds=30,
    credentials=None,
):
    org, actor = _actor(expected_actor_id)
    with transaction.atomic():
        site = sites.site_row(site_id, org, locked=True)
        sites._version(site, expected_site_version)
        row = NvkeTemporalAuthority.objects.select_for_update().filter(site=site).first()
        if row is None:
            if type(expected_version) is not int or expected_version != 0:
                raise sites.NvkeSiteError("VERSION_MISMATCH", "Native Temporal authority changed")
            row = NvkeTemporalAuthority(site=site, created_by=actor)
        else:
            sites._version(row, expected_version)
        if type(use_tls) is not bool or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
            raise _invalid()
        row.address, row.namespace, row.task_queue = _address(address), _name(namespace), _name(task_queue)
        row.server_name = _host(server_name) if server_name else ""
        row.use_tls, row.timeout_seconds = use_tls, timeout_seconds
        plaintext = _bundle(credentials) if credentials is not None else None
        carries_credentials = bool(row.credential_ciphertext) if plaintext is None else plaintext != b"{}"
        if not use_tls and (carries_credentials or row.server_name):
            raise sites.NvkeSiteError(
                "INVALID_INPUT", "Plaintext native Temporal cannot carry credentials or a TLS server name"
            )
        if plaintext is not None:
            sealed = encrypt_at_rest(plaintext) if plaintext != b"{}" else None
            row.credential_backend_kind = sealed.backend_kind if sealed else ""
            row.credential_ciphertext = sealed.backend_ref if sealed else b""
        row.is_enabled, row.verified_at, row.verification_code = True, None, ""
        row.updated_by = actor
        row.save()
        site.updated_by = actor
        site.save(update_fields=["updated_by", "updated_at", "version"])
        return row


def set_enabled(
    *, site_id, expected_actor_id, expected_site_version, expected_version, enabled=None, remove=False
):
    org, actor = _actor(expected_actor_id)
    with transaction.atomic():
        site = sites.site_row(site_id, org, locked=True)
        sites._version(site, expected_site_version)
        row = NvkeTemporalAuthority.objects.select_for_update().filter(site=site).first()
        if row is None:
            raise sites.NvkeSiteError("NOT_FOUND", "Native Temporal authority not found")
        sites._version(row, expected_version)
        if remove:
            row.is_enabled, row.verified_at, row.verification_code = False, None, ""
            row.deleted_at, row.deleted_by, row.updated_by = timezone.now(), actor, actor
            row.save()
        else:
            if type(enabled) is not bool:
                raise _invalid()
            row.is_enabled, row.verified_at, row.verification_code = enabled, None, ""
            row.updated_by = actor
            row.save()
        site.updated_by = actor
        site.save(update_fields=["updated_by", "updated_at", "version"])
        return row


def digest(row):
    return hashlib.sha256(
        json.dumps(
            {
                "id": str(row.guid),
                "site": str(row.site.guid),
                "version": row.version,
                "address": row.address,
                "namespace": row.namespace,
                "queue": row.task_queue,
                "server_name": row.server_name,
                "tls": row.use_tls,
                "enabled": row.is_enabled,
                "timeout": row.timeout_seconds,
                "backend": row.credential_backend_kind,
                "ciphertext": hashlib.sha256(bytes(row.credential_ciphertext)).hexdigest(),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


async def connect_native(row, checkpoint):
    await sync_to_async(checkpoint, thread_sensitive=True)()
    address, namespace = _address(row.address), _name(row.namespace)
    _name(row.task_queue)
    if row.server_name:
        _host(row.server_name)
    if (
        type(row.use_tls) is not bool
        or type(row.timeout_seconds) is not int
        or not 1 <= row.timeout_seconds <= 300
    ):
        raise _invalid()
    credentials = await sync_to_async(credentials_for, thread_sensitive=True)(row)
    if not row.use_tls and (credentials or row.server_name):
        raise sites.NvkeSiteError(
            "CREDENTIALS_UNAVAILABLE", "Plaintext native Temporal cannot carry credentials"
        )
    tls = (
        TLSConfig(
            server_root_ca_cert=credentials.get("ca_pem", "").encode() or None,
            domain=row.server_name or None,
            client_cert=credentials.get("client_certificate_pem", "").encode() or None,
            client_private_key=credentials.get("client_key_pem", "").encode() or None,
        )
        if row.use_tls
        else False
    )
    await sync_to_async(checkpoint, thread_sensitive=True)()
    try:
        client = await asyncio.wait_for(
            Client.connect(address, namespace=namespace, api_key=credentials.get("api_key") or None, tls=tls),
            row.timeout_seconds,
        )
    except Exception:
        raise sites.NvkeSiteError(
            "NATIVE_TEMPORAL_UNAVAILABLE",
            "The configured native Temporal connection could not be established",
        ) from None
    await sync_to_async(checkpoint, thread_sensitive=True)()
    return client


def verify(*, site_id, expected_actor_id, expected_site_version, expected_version):
    org, actor = _actor(expected_actor_id)
    site = sites.site_row(site_id, org)
    sites._version(site, expected_site_version)
    row = NvkeTemporalAuthority.objects.select_related("site").filter(site=site).first()
    if row is None:
        raise sites.NvkeSiteError("NOT_FOUND", "Native Temporal authority not found")
    sites._version(row, expected_version)
    original = digest(row)

    def checkpoint():
        current_org, current_actor = _actor(expected_actor_id)
        current_site = sites.site_row(site_id, current_org)
        current = (
            NvkeTemporalAuthority.objects.select_related("site")
            .filter(pk=row.pk, site=current_site, is_enabled=True)
            .first()
        )
        if (
            current_actor.pk != actor.pk
            or not current_site.is_active
            or current_site.version != expected_site_version
            or current is None
            or digest(current) != original
        ):
            raise sites.NvkeSiteError(
                "SOURCE_CHANGED", "Native Temporal authority changed before verification"
            )

    async def inspect_namespace():
        client = await connect_native(row, checkpoint)
        await sync_to_async(checkpoint, thread_sensitive=True)()
        try:
            response = await client.workflow_service.describe_namespace(
                DescribeNamespaceRequest(namespace=row.namespace),
                retry=False,
                timeout=timedelta(seconds=row.timeout_seconds),
            )
        except Exception:
            raise sites.NvkeSiteError(
                "NATIVE_TEMPORAL_UNAVAILABLE",
                "The configured native Temporal namespace could not be verified",
            ) from None
        if response.namespace_info.name != row.namespace:
            raise sites.NvkeSiteError("SOURCE_CHANGED", "Native Temporal namespace identity did not match")
        await sync_to_async(checkpoint, thread_sensitive=True)()

    async_to_sync(inspect_namespace)()
    with transaction.atomic():
        sites.site_row(site_id, org, locked=True)
        checkpoint()
        updated = NvkeTemporalAuthority.objects.filter(
            pk=row.pk, version=row.version, is_enabled=True
        ).update(verified_at=timezone.now(), verification_code="NAMESPACE_VERIFIED")
        if updated != 1:
            raise sites.NvkeSiteError(
                "SOURCE_CHANGED", "Native Temporal authority changed before verification"
            )
        row.refresh_from_db()
    return row
