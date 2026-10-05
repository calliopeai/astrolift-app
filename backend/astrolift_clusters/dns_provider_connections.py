"""Fresh operator-admitted read-only DNS connection and attachment operations."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from contextlib import contextmanager
from dataclasses import dataclass, replace
from types import SimpleNamespace
from uuid import UUID

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from astrolift_clusters.cloudflare_dns_transport import CloudflareReadClient, DnsConnectionError, hostname
from astrolift_clusters.models import DnsProviderConnection, ManagedDomain
from astrolift_clusters.scopes import _org_scope
from astrolift_identity import abac
from astrolift_identity.api_tokens import get_current_api_token, token_scope_allows_permission
from astrolift_identity.models import Organization
from astrolift_services.hosting_authority import current_host_operator
from core.permissions import Permission, check_permission
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest
from core.tenancy import get_current_tenant


@contextmanager
def admitted(request, *, write=False):
    permission = Permission.PROVIDER_PLUGIN_CONFIGURE if write else Permission.PROVIDER_PLUGIN_READ
    original = get_current_api_token()
    if original is not None and not token_scope_allows_permission(original, permission.value):
        raise DnsConnectionError("PERMISSION_DENIED")
    with current_host_operator(request=request) as actor:
        fresh_token = get_current_api_token()
        if original is not None and (
            fresh_token is None
            or fresh_token.token_hash != original.token_hash
            or fresh_token.guid != original.guid
        ):
            raise DnsConnectionError("PERMISSION_DENIED")
        if original is None and hasattr(request, "session"):
            from core.current_session import fresh_authenticated_session

            bag = fresh_authenticated_session(request, actor_user_id=actor.pk, permission=permission)
            attrs = abac.attributes_from_request(SimpleNamespace(META=request.META, session=bag), actor.pk)
        else:
            attrs = abac.attributes_for(actor.pk)
        with abac.request_attributes(replace(attrs, cache={})):
            check_permission(permission, scope=_org_scope(permission))
            tenant = get_current_tenant()
            org = Organization.objects.filter(pk=tenant.organization_id, deleted_at__isnull=True).first()
            if org is None:
                raise DnsConnectionError("PERMISSION_DENIED")
            yield org, actor


def rate_limit(org, actor):
    # A per-process LocMem install has a per-process limiter, not a fleet quota.
    key = f"dns-connections:{org.pk}:{actor.pk}:{int(timezone.now().timestamp()) // 60}"
    if cache.add(key, 1, 70):
        return
    if cache.incr(key) > 30:
        raise DnsConnectionError("RATE_LIMITED")


def guid(value):
    try:
        result = UUID(str(value))
        if result.int:
            return result
    except (ValueError, TypeError, AttributeError):
        pass
    raise DnsConnectionError("INVALID_ID")


def name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 128 or any(ord(x) < 32 for x in value):
        raise DnsConnectionError("INVALID_NAME")
    return value.strip()


def connection_row(org, connection_id, expected_version, *, locked=False, allow_expired=False):
    query = DnsProviderConnection.objects
    if locked:
        query = query.select_for_update()
    row = query.filter(guid=guid(connection_id), organization=org, provider="CLOUDFLARE").first()
    if row is None:
        raise DnsConnectionError("NOT_FOUND")
    if type(expected_version) is not int or row.version != expected_version:
        raise DnsConnectionError("VERSION_MISMATCH")
    if row.state != "ACTIVE":
        raise DnsConnectionError("DISCONNECTED")
    if not allow_expired and row.expires_at is not None and row.expires_at <= timezone.now():
        raise DnsConnectionError("EXPIRED")
    return row


def _credential(row):
    try:
        return decrypt(EncryptedSecret(row.secret_backend_kind, bytes(row.secret_ciphertext))).decode()
    except Exception:
        raise DnsConnectionError("CREDENTIAL_UNAVAILABLE") from None


def observe_connection(request, connection_id, expected_version, *, write=False, checkpoint=None):
    with admitted(request, write=write) as (org, actor):
        rate_limit(org, actor)
        row = connection_row(org, connection_id, expected_version)
        original = (
            row.pk,
            row.organization_id,
            row.provider,
            row.auth_method,
            row.secret_backend_kind,
            bytes(row.secret_ciphertext),
        )

    def current():
        if checkpoint is not None:
            checkpoint()
        with admitted(request, write=write) as (fresh_org, _):
            fresh = connection_row(fresh_org, connection_id, expected_version)
            if (
                fresh.pk,
                fresh.organization_id,
                fresh.provider,
                fresh.auth_method,
                fresh.secret_backend_kind,
                bytes(fresh.secret_ciphertext),
            ) != original:
                raise DnsConnectionError("CONNECTION_CHANGED")

    current()
    return row, CloudflareReadClient(_credential(row), current), current


@dataclass(frozen=True)
class DnsZoneObservation:
    connection_id: UUID
    connection_version: int
    id: str
    name: str
    account_id: str
    status: str
    name_servers: tuple[str, ...]


@dataclass(frozen=True)
class DnsRecordObservation:
    id: str
    name: str
    type: str
    content: str
    ttl: int
    proxied: bool
    priority: int | None


def observe_zone(
    request, connection_guid, expected_version, zone_id, zone_name, *, checkpoint=None
) -> DnsZoneObservation:
    """Typed normalized metadata only. This verifies access, not zone ownership/TXT proof."""
    _, client, current = observe_connection(request, connection_guid, expected_version, checkpoint=checkpoint)
    result = client.zone(zone_id, zone_name)
    current()
    return DnsZoneObservation(
        connection_id=guid(connection_guid),
        connection_version=expected_version,
        **{**result, "name_servers": tuple(result["name_servers"])},
    )


def observe_zone_records(
    request, connection_guid, expected_version, zone_id, zone_name, *, checkpoint=None
) -> tuple[DnsRecordObservation, ...]:
    _, client, current = observe_connection(request, connection_guid, expected_version, checkpoint=checkpoint)
    result = client.records(zone_id, zone_name)
    current()
    return tuple(DnsRecordObservation(**row) for row in result)


def connect_token(
    request,
    organization_id,
    connection_name,
    token,
    *,
    auth_method="API_TOKEN",
    expires_at=None,
    oauth_client_id="",
    checkpoint=None,
):
    connection_name = name(connection_name)
    with admitted(request, write=True) as (org, actor):
        if org.guid != guid(organization_id):
            raise DnsConnectionError("PERMISSION_DENIED")
        original = (org.pk, actor.pk)
        rate_limit(org, actor)

    def current():
        if checkpoint is not None:
            checkpoint()
        with admitted(request, write=True) as (fresh_org, fresh_actor):
            if (fresh_org.pk, fresh_actor.pk) != original:
                raise DnsConnectionError("PERMISSION_DENIED")

    # The read succeeds only if the entire bounded zone inventory is complete.
    CloudflareReadClient(token, current).zones()
    encrypted = encrypt_at_rest(token.encode())
    with transaction.atomic():
        current()
        row = DnsProviderConnection.objects.create(
            organization=org,
            name=connection_name,
            auth_method=auth_method,
            oauth_client_id=oauth_client_id,
            secret_backend_kind=encrypted.backend_kind,
            secret_ciphertext=encrypted.backend_ref,
            verified_at=timezone.now(),
            expires_at=expires_at,
            created_by=actor,
        )
        current()
        return row


def retest(request, connection_id, expected_version):
    row, client, current = observe_connection(request, connection_id, expected_version, write=True)
    client.zones()
    with transaction.atomic():
        connection_row(row.organization, connection_id, expected_version, locked=True)
        current()
        row.verified_at = timezone.now()
        row.save(update_fields=["verified_at", "version", "updated_at"])
    return row


def disconnect(request, connection_id, expected_version):
    access = None
    with transaction.atomic():
        with admitted(request, write=True) as (org, _):
            row = connection_row(org, connection_id, expected_version, locked=True, allow_expired=True)
            with admitted(request, write=True):
                pass
            if row.auth_method == "OAUTH":
                try:
                    access = _credential(row)
                except DnsConnectionError:
                    # A missing encryption key must not prevent local revocation.
                    pass
            # Local disconnection also works for expired access tokens.
            row.state = "DISCONNECTED"
            row.revocation_state = "OAUTH_UNCONFIRMED" if row.auth_method == "OAUTH" else "LOCAL_ONLY"
            row.secret_ciphertext = b""
            row.refresh_ciphertext = b""
            row.save(
                update_fields=[
                    "state",
                    "revocation_state",
                    "secret_ciphertext",
                    "refresh_ciphertext",
                    "version",
                    "updated_at",
                ]
            )
    if access is None:
        return row
    # Local revocation commits first. An uncertain remote response never restores
    # the secret, retries the POST, or turns an accepted disconnect into failure.
    disconnected_version = row.version

    def current():
        with admitted(request, write=True) as (fresh_org, _):
            fresh = DnsProviderConnection.objects.filter(pk=row.pk, organization=fresh_org).first()
            if (
                fresh is None
                or fresh.state != "DISCONNECTED"
                or fresh.version != disconnected_version
                or fresh.oauth_client_id != row.oauth_client_id
            ):
                raise DnsConnectionError("CONNECTION_CHANGED")
            config = oauth_configuration()
            if config[0] != row.oauth_client_id:
                raise DnsConnectionError("OAUTH_CLIENT_CHANGED")
            return config

    try:
        config = current()
        CloudflareReadClient(None, current).json(
            "/oauth2/revoke",
            form={
                "token": access,
                "token_type_hint": "access_token",
                "client_id": config[0],
                "client_secret": config[1],
            },
        )
        with transaction.atomic():
            fresh = DnsProviderConnection.objects.select_for_update().get(pk=row.pk)
            current()
            fresh.revocation_state = "OAUTH_REVOKED"
            fresh.save(update_fields=["revocation_state", "version", "updated_at"])
        row = fresh
    except Exception:
        # A reply lost after provider acceptance is deliberately unconfirmed.
        pass
    return row


def verify_read_only_domain(request, domain):
    """Existing TXT challenge, with fresh admission and exact retained row fences.

    Verification proves the observed challenge only; it never creates a writer.
    """
    from _sdk._dns_probe import DnsResolveError, lookup_txt

    snapshot = (
        domain.pk,
        domain.version,
        domain.zone,
        domain.verification_token,
        domain.dns_provider_connection_id,
        domain.dns_provider_zone_id,
        domain.dns_provider_connection_version,
    )

    def current(*, locked=False):
        with admitted(request, write=True) as (org, _):
            query = ManagedDomain.objects.select_for_update() if locked else ManagedDomain.objects
            fresh = query.filter(pk=domain.pk, organization=org, dns_driver="cloudflare_read_only").first()
            if (
                fresh is None
                or (
                    fresh.pk,
                    fresh.version,
                    fresh.zone,
                    fresh.verification_token,
                    fresh.dns_provider_connection_id,
                    fresh.dns_provider_zone_id,
                    fresh.dns_provider_connection_version,
                )
                != snapshot
            ):
                raise DnsConnectionError("DOMAIN_CHANGED")
            return fresh

    current()
    if domain.verification_state != ManagedDomain.VerificationState.PENDING:
        return True
    try:
        values = lookup_txt("_astrolift-challenge." + hostname(domain.zone))
    except DnsResolveError:
        current()
        return False
    current()
    if not any(domain.verification_token in value for value in values):
        return False
    with transaction.atomic():
        fresh = current(locked=True)
        fresh.verification_state = ManagedDomain.VerificationState.VERIFIED
        fresh.verified_at = timezone.now()
        fresh.save(update_fields=["verification_state", "verified_at", "version", "updated_at"])
    return True


def attach_zone(request, domain_id, domain_version, connection_id, connection_version, zone_id, zone_name):
    zone_name = hostname(zone_name)
    with admitted(request, write=True) as (org, _):
        domain = ManagedDomain.objects.filter(guid=guid(domain_id), organization=org).first()
        if domain is None:
            raise DnsConnectionError("NOT_FOUND")
        if domain.version != domain_version or hostname(domain.zone) != zone_name:
            raise DnsConnectionError("DOMAIN_CHANGED")
        snapshot = (
            domain.zone,
            domain.dns_driver,
            domain.dns_config,
            domain.provision_zone_id,
            domain.verification_state,
        )
    connection, client, current = observe_connection(request, connection_id, connection_version, write=True)
    zone = client.zone(zone_id, zone_name)
    with transaction.atomic():
        # Parent domain before connection. No native request occurs in this transaction.
        fresh = (
            ManagedDomain.objects.select_for_update().filter(guid=guid(domain_id), organization=org).first()
        )
        if (
            fresh is None
            or fresh.version != domain_version
            or (
                fresh.zone,
                fresh.dns_driver,
                fresh.dns_config,
                fresh.provision_zone_id,
                fresh.verification_state,
            )
            != snapshot
        ):
            raise DnsConnectionError("DOMAIN_CHANGED")
        connection_row(org, connection_id, connection_version, locked=True)
        current()
        fresh.dns_provider_connection = connection
        fresh.dns_provider_zone_id = zone["id"]
        fresh.dns_provider_connection_version = connection.version
        fresh.save(
            update_fields=[
                "dns_provider_connection",
                "dns_provider_zone_id",
                "dns_provider_connection_version",
                "version",
                "updated_at",
            ]
        )
    return fresh, zone


def register_zone(request, connection_id, connection_version, zone_id, zone_name):
    """Register a read-only external zone without starting DNS/certificate workflows."""
    zone_name = hostname(zone_name)
    connection, client, current = observe_connection(request, connection_id, connection_version, write=True)
    zone = client.zone(zone_id, zone_name)
    with transaction.atomic():
        with admitted(request, write=True) as (org, actor):
            connection_row(org, connection_id, connection_version, locked=True)
            current()
            if ManagedDomain.objects.filter(zone=zone_name).exists():
                raise DnsConnectionError("DOMAIN_ALREADY_REGISTERED")
            domain = ManagedDomain.objects.create(
                organization=org,
                zone=zone_name,
                dns_driver="cloudflare_read_only",
                default_for=ManagedDomain.DefaultFor.NONE,
                verification_state=ManagedDomain.VerificationState.PENDING,
                verification_token=secrets.token_hex(16),
                created_by=actor,
                dns_provider_connection=connection,
                dns_provider_zone_id=zone["id"],
                dns_provider_connection_version=connection.version,
            )
    return domain, zone


def oauth_configuration():
    client_id = getattr(settings, "ASTROLIFT_CLOUDFLARE_OAUTH_CLIENT_ID", "")
    secret = getattr(settings, "ASTROLIFT_CLOUDFLARE_OAUTH_CLIENT_SECRET", "")
    base = getattr(settings, "APP_BASE_URL", "").rstrip("/")
    scopes = getattr(settings, "ASTROLIFT_CLOUDFLARE_OAUTH_READ_SCOPES", ())
    if (
        not isinstance(client_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", client_id)
        or not isinstance(secret, str)
        or not 16 <= len(secret) <= 4096
    ):
        raise DnsConnectionError("OAUTH_CLIENT_NOT_CONFIGURED")
    from urllib.parse import urlsplit

    parsed = urlsplit(base)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise DnsConnectionError("OAUTH_CALLBACK_NOT_CONFIGURED")
    if (
        not isinstance(scopes, (tuple, list))
        or len(scopes) != 2
        or len(set(scopes)) != 2
        or any(not isinstance(x, str) or not re.fullmatch(r"[a-z][a-z0-9.-]{0,96}\.read", x) for x in scopes)
    ):
        raise DnsConnectionError("OAUTH_READ_SCOPES_NOT_CONFIGURED")
    return client_id, secret, base + "/api/clusters/dns/cloudflare/callback/", tuple(scopes)


def session_fingerprint(request):
    key = getattr(getattr(request, "session", None), "session_key", None)
    if get_current_api_token() is not None or not key:
        raise DnsConnectionError("BROWSER_SESSION_REQUIRED")
    return hmac.new(
        settings.SECRET_KEY.encode(), b"dns-oauth-session:" + key.encode(), hashlib.sha256
    ).hexdigest()
