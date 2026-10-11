"""Single-use browser/org-bound Cloudflare Authorization Code + PKCE flow."""

import base64
import hashlib
import secrets
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from urllib.parse import urlencode

from django.db import transaction
from django.http import HttpResponseRedirect
from django.utils import timezone
from django.views.decorators.http import require_GET

from astrolift_clusters.cloudflare_dns_transport import CloudflareReadClient, DnsConnectionError, token_text
from astrolift_clusters.dns_provider_connections import (
    admitted,
    connect_token,
    name,
    oauth_configuration,
    rate_limit,
    session_fingerprint,
)
from astrolift_clusters.models import DnsProviderOAuthAttempt
from core.mutations import AuditEntry, emit_audit
from core.permissions import Permission, route_auth
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest
from core.tenancy import get_current_tenant, tenant_context


def configuration_sha(config):
    return hashlib.sha256("\0".join((config[0], config[1], config[2], *config[3])).encode()).hexdigest()


@contextmanager
def callback_tenant(request):
    """A browser redirect cannot supply the GraphQL organization header.

    Restore only the original server-owned attempt scope after fresh actual
    session authentication. This is selection metadata, never an authority
    substitute: complete() still performs every current operator/org check.
    Explicit current org selections remain authoritative and cannot be replaced.
    """
    tenant = get_current_tenant()
    if tenant is not None and tenant.organization_id is not None:
        yield
        return
    from core.current_session import fresh_authenticated_session

    session = session_fingerprint(request)
    actor_id = getattr(getattr(request, "user", None), "pk", None)
    if tenant is None or actor_id is None or tenant.actor_user_id != actor_id:
        raise DnsConnectionError("OAUTH_STATE_UNAVAILABLE")
    bag = fresh_authenticated_session(
        request, actor_user_id=actor_id, permission=Permission.PROVIDER_PLUGIN_CONFIGURE
    )
    if request.META.get("HTTP_X_ASTROLIFT_ORGANIZATION") or bag.get("organization_id") is not None:
        raise DnsConnectionError("OAUTH_STATE_UNAVAILABLE")
    state = request.GET.get("state", "")
    if not isinstance(state, str) or not 32 <= len(state) <= 128:
        raise DnsConnectionError("OAUTH_STATE_UNAVAILABLE")
    attempt = DnsProviderOAuthAttempt.objects.filter(
        state_sha256=hashlib.sha256(state.encode()).hexdigest(),
        actor_id=actor_id,
        consumed_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).first()
    if attempt is None or not secrets.compare_digest(attempt.session_hmac, session):
        raise DnsConnectionError("OAUTH_STATE_UNAVAILABLE")
    with tenant_context(replace(tenant, organization_id=attempt.organization_id)):
        yield


def begin(request, organization_guid, connection_name):
    from astrolift_clusters.dns_provider_connections import guid

    with admitted(request, write=True) as (org, actor):
        if org.guid != guid(organization_guid):
            raise DnsConnectionError("PERMISSION_DENIED")
        rate_limit(org, actor)
        session = session_fingerprint(request)
        config = oauth_configuration()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        encrypted = encrypt_at_rest(verifier.encode())
        with transaction.atomic():
            with admitted(request, write=True):
                DnsProviderOAuthAttempt.objects.create(
                    organization=org,
                    actor=actor,
                    name=name(connection_name),
                    state_sha256=hashlib.sha256(state.encode()).hexdigest(),
                    session_hmac=session,
                    client_sha256=configuration_sha(config),
                    verifier_backend_kind=encrypted.backend_kind,
                    verifier_ciphertext=encrypted.backend_ref,
                    expires_at=timezone.now() + timedelta(minutes=10),
                )
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return "https://dash.cloudflare.com/oauth2/auth?" + urlencode(
        {
            "client_id": config[0],
            "redirect_uri": config[2],
            "response_type": "code",
            "scope": " ".join(config[3]),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )


def complete(request):
    state, code = request.GET.get("state", ""), request.GET.get("code", "")
    if (
        not isinstance(state, str)
        or not 32 <= len(state) <= 128
        or not isinstance(code, str)
        or not 1 <= len(code) <= 2048
    ):
        raise DnsConnectionError("OAUTH_RESPONSE_INVALID")
    with admitted(request, write=True) as (org, actor):
        config = oauth_configuration()
        session = session_fingerprint(request)
        rate_limit(org, actor)
        with transaction.atomic():
            row = (
                DnsProviderOAuthAttempt.objects.select_for_update()
                .filter(
                    state_sha256=hashlib.sha256(state.encode()).hexdigest(),
                    organization=org,
                    actor=actor,
                    consumed_at__isnull=True,
                    expires_at__gt=timezone.now(),
                )
                .first()
            )
            if (
                row is None
                or not secrets.compare_digest(row.session_hmac, session)
                or row.client_sha256 != configuration_sha(config)
            ):
                raise DnsConnectionError("OAUTH_STATE_UNAVAILABLE")
            with admitted(request, write=True):
                encrypted = EncryptedSecret(row.verifier_backend_kind, bytes(row.verifier_ciphertext))
                row.consumed_at = timezone.now()
                row.verifier_ciphertext = b""
                row.save(update_fields=["consumed_at", "verifier_ciphertext", "version", "updated_at"])

        # The committed single-use reservation is not retried on an ambiguous exchange.
        def current():
            with admitted(request, write=True) as (fresh_org, fresh_actor):
                if (
                    (fresh_org.pk, fresh_actor.pk) != (org.pk, actor.pk)
                    or session_fingerprint(request) != session
                    or configuration_sha(oauth_configuration()) != row.client_sha256
                ):
                    raise DnsConnectionError("PERMISSION_DENIED")

        current()
        try:
            verifier = decrypt(encrypted).decode()
        except Exception:
            raise DnsConnectionError("CREDENTIAL_UNAVAILABLE") from None
        payload = CloudflareReadClient(None, current).json(
            "/oauth2/token",
            form={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": config[2],
                "client_id": config[0],
                "client_secret": config[1],
                "code_verifier": verifier,
            },
        )
        access = token_text(payload.get("access_token"))
        expires = payload.get("expires_in")
        granted = payload.get("scope")
        if (
            payload.get("token_type", "").lower() != "bearer"
            or type(expires) is not int
            or not 1 <= expires <= 86400
            or not isinstance(granted, str)
            or set(granted.split()) != set(config[3])
        ):
            raise DnsConnectionError("OAUTH_TOKEN_RESPONSE_INVALID")
        current()
        return connect_token(
            request,
            org.guid,
            row.name,
            access,
            auth_method="OAUTH",
            oauth_client_id=config[0],
            checkpoint=current,
            expires_at=timezone.now() + timedelta(seconds=expires),
        )


@require_GET
@route_auth(
    credential="fresh original persisted browser session and single-use PKCE state",
    scope="original server-owned OAuth attempt organization; explicit selected organization cannot be replaced",
    permissions=[Permission.PROVIDER_PLUGIN_CONFIGURE],
)
def callback(request):
    # Do not render provider code/token/error bodies or accept a return URL.
    # Upstream access logs must redact this callback's query string at installation.
    from opentelemetry import trace

    span = trace.get_current_span()
    span.set_attribute("http.url", request.build_absolute_uri(request.path))
    span.set_attribute("url.full", request.build_absolute_uri(request.path))
    span.set_attribute("url.query", "")
    span.set_attribute("http.target", request.path)
    try:
        with callback_tenant(request):
            row = complete(request)
        outcome = "saved"
    except Exception:
        row = None
        outcome = "unconfirmed"
    tenant = get_current_tenant()
    emit_audit(
        AuditEntry(
            actor_user_id=row.created_by_id if row else tenant.actor_user_id if tenant else None,
            organization_id=row.organization_id if row else tenant.organization_id if tenant else None,
            action="dns.connection.oauth.complete",
            decision="ALLOW" if row else "DENY",
            target_kind="DnsProviderConnection" if row else None,
            target_id=str(row.guid) if row else None,
            duration_ms=0,
            permissions=("provider_plugin.configure",),
            error_code=None if row else "UNCONFIRMED",
        )
    )
    response = HttpResponseRedirect("/domains?connectionOutcome=" + outcome)
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response
