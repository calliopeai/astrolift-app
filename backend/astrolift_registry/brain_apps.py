"""Apps hosted on a brain (#2243): one template, one binding type.

A brain-backed app is an ordinary app whose manifest names the brain it
reads (``[brain] slug = "..."``) and which holds a ``BrainAppBinding`` to it,
granted by someone who can read that brain. The manifest says which brain;
the binding says it may.

Each deploy gives the environment a ``astrolift-brain-<app>-<env>`` Secret,
injected through ``envFrom`` like every other platform Secret:

* ``BRAIN_SLUG``, ``BRAIN_MCP_URL`` (the first endpoint) and
  ``BRAIN_MCP_URLS`` (all, comma-separated),
* ``BRAIN_TOKEN_HEADER`` and ``BRAIN_TOKEN``, a read token the cluster edge
  admits to that brain only (``brain_tokens``), whose subject is the app.

The token is signed by a key of the binding's own, minted once per deploy,
so a redeploy rotates it. The previous deploy's key stays published, since
pods of the release being replaced still carry its token; the one before is
deleted. Deleting the binding, the app or the brain unpublishes every key of
the binding, and the edge stops admitting the app within its JWKS cache.

Each deploy checks again that its deployer (the person who triggered it, or
else whoever granted the binding) can still read the brain.
"""

from __future__ import annotations

import datetime as dt
import secrets
from typing import Any

from django.db.models import Q
from django.utils import timezone

from astrolift_registry.models import BrainAppBinding, BrainTokenKey, RegisteredApp

TEMPLATE_KEY = "brain-app"
DEFAULT_PORT = 8080
# Revocation is by unpublishing the key, so the expiry is only a backstop.
APP_TOKEN_TTL = dt.timedelta(days=365)
KEY_ANNOTATION = "astrolift.io/brain-token-key"
ENV_KEYS = ("BRAIN_SLUG", "BRAIN_MCP_URL", "BRAIN_MCP_URLS", "BRAIN_TOKEN_HEADER", "BRAIN_TOKEN")


class BrainAppError(Exception):
    pass


def brain_app_manifest(*, slug: str, brain_slug: str, image: str, port: int = DEFAULT_PORT) -> str:
    """The brain-app template: one public web workload running ``image``,
    reading ``brain_slug``. Rendered with ``tomli_w`` so no value can break
    out of its string."""
    import tomli_w

    return tomli_w.dumps(
        {
            "name": slug,
            "brain": {"slug": brain_slug},
            "workloads": [
                {
                    "name": "web",
                    "kind": "deployment",
                    "is_public": True,
                    "containers": [{"name": "app", "is_primary": True, "image_ref": image, "port": port}],
                }
            ],
        }
    )


def mcp_urls(brain: RegisteredApp) -> list[str]:
    """Where the brain serves MCP: ``/mcp`` on each served environment."""
    from astrolift_registry.brains import describe_brain

    return [f"{env['url'].rstrip('/')}/mcp" for env in describe_brain(brain)["environments"]]


def readable_brain_for(user: Any, organization_id: int, slug: str) -> RegisteredApp | None:
    """The brain ``slug`` if ``user`` can read it, by the same rule as the
    Brains screen (``visible_brains``), evaluated as that user."""
    from astrolift_registry.brains import visible_brains
    from core.tenancy import TenantContext, tenant_context

    if user is None or not slug:
        return None
    with tenant_context(TenantContext(organization_id=organization_id, actor_user_id=user.pk)):
        return visible_brains().filter(organization_id=organization_id, slug=slug).first()


def live_bindings():
    """Bindings whose app and brain are both live."""
    return BrainAppBinding.objects.filter(
        registered_app__deleted_at__isnull=True,
        brain__deleted_at__isnull=True,
        brain__archived_at__isnull=True,
    )


def live_binding_keys():
    """The binding keys the JWKS publishes: those of live bindings."""
    return BrainTokenKey.objects.filter(binding__in=live_bindings())


def declared_brain(app: RegisteredApp) -> str:
    """The brain slug ``app``'s stored manifest names, ``""`` for none."""
    from astrolift_manifest.parser import ManifestError, parse_raw

    try:
        return parse_raw(app.manifest_raw or "").brain if (app.manifest_raw or "").strip() else ""
    except ManifestError:
        return ""


def binding_for(app: RegisteredApp) -> BrainAppBinding | None:
    """The live binding to the brain ``app``'s manifest names, if both exist."""
    slug = declared_brain(app)
    if not slug:
        return None
    return (
        live_bindings()
        .filter(
            registered_app=app,
            organization_id=app.organization_id,
            brain__organization_id=app.organization_id,
            brain__slug=slug,
        )
        .select_related("brain", "granted_by")
        .first()
    )


def brain_secret_name(app_slug: str, environment_name: str) -> str:
    from _sdk.k8s_naming import dns_label

    return dns_label("astrolift", "brain", app_slug, environment_name)


def deployment_brain_secret(deployment: Any) -> str | None:
    """The brain Secret ``deployment``'s workloads read, or ``None``."""
    app = deployment.registered_app
    if binding_for(app) is None:
        return None
    return brain_secret_name(app.slug, deployment.app_environment.name)


def _deployment_key(binding: BrainAppBinding, deployment: Any) -> BrainTokenKey:
    """This deployment's key, minted on first use. Re-running a deploy step
    reuses it, so only a new deploy rotates."""
    from django.db import transaction

    from astrolift_lifecycle.models import Deployment
    from astrolift_registry.brain_tokens import _new_key

    with transaction.atomic():
        BrainAppBinding.objects.select_for_update().filter(pk=binding.pk).first()
        existing = BrainTokenKey.objects.filter(binding=binding, deployment=deployment).first()
        if existing is not None:
            return existing
        same_env = BrainTokenKey.objects.filter(
            binding=binding, deployment__app_environment_id=deployment.app_environment_id
        ).order_by("-created_at", "-pk")
        # Keep the release being replaced (the newest key) and the release
        # still serving (a running deploy's key, when a deploy since failed);
        # drop everything older.
        stale = [
            row.pk
            for row in same_env.select_related("deployment")[1:]
            if row.deployment.status != Deployment.Status.RUNNING.value
        ]
        if stale:
            BrainTokenKey.objects.filter(pk__in=stale).delete()
        return _new_key(binding=binding, deployment=deployment)


def issue_app_token(binding: BrainAppBinding, deployment: Any, *, now: dt.datetime | None = None) -> str:
    """A read token for the app of ``binding``, signed by this deployment's key.
    Typed (``sub_kind`` ``app``) when the brain issues typed tokens (#2470),
    and the key then records it."""
    from types import SimpleNamespace

    import jwt

    from astrolift_registry.brain_tokens import (
        ALGORITHM,
        SCOPE,
        TYPED_CONTRACT,
        TypedSubject,
        check_typed_size,
        issuer,
        typed_claims,
        typed_issuance,
    )
    from core.secrets import EncryptedSecret, decrypt

    now = now or timezone.now()
    key = _deployment_key(binding, deployment)
    pem = decrypt(
        EncryptedSecret(
            backend_kind=key.private_key_backend_kind, backend_ref=bytes(key.private_key_ciphertext)
        )
    )
    claims = {
        "iss": issuer(),
        "aud": str(binding.brain.guid),
        "sub": f"app:{binding.registered_app.guid}",
        "scope": SCOPE,
        "env": deployment.app_environment.name,
        "iat": int(now.timestamp()),
        "exp": int((now + APP_TOKEN_TTL).timestamp()),
        "jti": secrets.token_hex(12),
    }
    typed = typed_issuance(binding.brain)
    if typed:
        app_guid = str(binding.registered_app.guid)
        subject = SimpleNamespace(pk=None, guid=claims["sub"], email="")
        claims.update(typed_claims(TypedSubject("app", guid=app_guid), brain=binding.brain, user=subject))
    token = jwt.encode(claims, pem, algorithm=ALGORITHM, headers={"kid": key.kid})
    if typed:
        check_typed_size(token)
        if key.typed_contract != TYPED_CONTRACT:
            key.typed_contract = TYPED_CONTRACT
            key.save(update_fields=["typed_contract", "updated_at", "version"])
    return token


def deployer(binding: BrainAppBinding, deployment: Any) -> Any:
    return getattr(deployment, "triggered_by_user", None) or binding.granted_by


def brain_secret(deployment: Any, namespace: str) -> dict[str, Any] | None:
    """The k8s Secret carrying the brain binding for ``deployment``, or
    ``None`` when the manifest names no brain. Raises ``BrainAppError`` when
    it names one the app is not bound to, the deployer can no longer read,
    or that is not served anywhere."""
    import base64

    from astrolift_registry.brain_tokens import BrainTokenError
    from providers.k8s_native.edge_gateway import BRAIN_TOKEN_HEADER

    app = deployment.registered_app
    slug = declared_brain(app)
    if not slug:
        return None
    binding = binding_for(app)
    if binding is None:
        raise BrainAppError(
            f"the manifest reads brain {slug!r} but the app is not bound to it; "
            "host the app on the brain from the Brains screen (hostAppOnBrain)"
        )
    if readable_brain_for(deployer(binding, deployment), app.organization_id, slug) is None:
        raise BrainAppError(f"the deployer can no longer read brain {slug!r}")
    urls = mcp_urls(binding.brain)
    if not urls:
        raise BrainAppError(f"brain {slug!r} is not served in any environment yet")
    try:
        token = issue_app_token(binding, deployment)
    except BrainTokenError as exc:
        raise BrainAppError(str(exc)) from exc
    values = {
        "BRAIN_SLUG": slug,
        "BRAIN_MCP_URL": urls[0],
        "BRAIN_MCP_URLS": ",".join(urls),
        "BRAIN_TOKEN_HEADER": BRAIN_TOKEN_HEADER,
        "BRAIN_TOKEN": token,
    }
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": brain_secret_name(app.slug, deployment.app_environment.name),
            "namespace": namespace,
            "labels": {"astrolift.io/managed-by": "astrolift", "astrolift.io/brain-for": app.slug},
        },
        "type": "Opaque",
        "data": {k: base64.b64encode(v.encode("utf-8")).decode("ascii") for k, v in values.items()},
    }


def stamp_brain_key(resources: list[dict[str, Any]], deployment: Any) -> None:
    """Mark long-running pod templates with this deployment's key, so a
    rotated token rolls the pods (a pod reads its Secret once, at start)."""
    if getattr(deployment, "pk", None) is None:
        return
    kid = (
        BrainTokenKey.objects.filter(deployment=deployment, binding__isnull=False)
        .values_list("kid", flat=True)
        .first()
    )
    if not kid:
        return
    for resource in resources:
        if resource.get("kind") in ("Deployment", "StatefulSet"):
            template = (resource.get("spec") or {}).get("template")
            if template is not None:
                template.setdefault("metadata", {}).setdefault("annotations", {})[KEY_ANNOTATION] = kid


def bind(*, app: RegisteredApp, brain: RegisteredApp, granted_by: Any) -> BrainAppBinding:
    return BrainAppBinding.objects.create(
        organization_id=app.organization_id, registered_app=app, brain=brain, granted_by=granted_by
    )


def revoke_app(registered_app_id: int) -> int:
    """Revoke every binding ``registered_app_id`` holds or is the brain of:
    soft-delete the bindings and delete their keys, so no token of theirs
    verifies again. The agent runs reading it as a brain are revoked too
    (#2432); the count is of app bindings."""
    from astrolift_registry.brain_runs import revoke_brain

    revoke_brain(registered_app_id)
    now = timezone.now()
    rows = list(
        BrainAppBinding.objects.filter(
            Q(registered_app_id=registered_app_id) | Q(brain_id=registered_app_id), deleted_at__isnull=True
        )
    )
    BrainTokenKey.objects.filter(binding__in=[row.pk for row in rows]).delete()
    for row in rows:
        row.deleted_at = now
        row.save(update_fields=["deleted_at", "updated_at", "version"])
    return len(rows)
