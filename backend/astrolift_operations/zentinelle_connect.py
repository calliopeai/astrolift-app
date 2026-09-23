"""Connect an organization to Zentinelle and run the Zentinelle gateway in its clusters (#1887).

Zentinelle is the control plane; its Go gateway is the data plane inside each
cluster. The wire contract is Zentinelle's ("Connected installs and clusters",
calliopeai/zentinelle#389):

* ``POST /api/zentinelle/v1/astrolift/connect`` exchanges a one-time
  enrollment code for the install credential (``sk_astroinst_``), once.
* With that credential, ``POST .../astrolift/clusters`` registers a cluster
  by id and answers with its gateway's credential (``sk_gateway_``), once.
  Registering the same cluster again mints a fresh one and keeps the earlier
  ones alive for ten minutes.
* ``POST .../clusters/<id>/rotate`` mints the next credential with an
  overlap, ``DELETE .../clusters/<id>`` revokes one cluster, and
  ``DELETE .../install`` revokes the install with everything it registered.
* ``POST .../agents`` mints a short-lived agent key for one task or box,
  ``POST .../agents/<id>/renew`` moves its expiry and ``DELETE
  .../agents/<id>`` revokes it (calliopeai/zentinelle#400, used by #1851).

The install credential is sealed with ``core.secrets``. A gateway credential
goes from Zentinelle's answer into the ``zentinelle-gateway-credential``
Secret and nowhere else: not the database, a log line, an audit row, an
error message or a GraphQL payload.

The gateway runs as Deployment + Service ``zentinelle-gateway`` in
``astrolift-system``, applied through the cluster driver the same way as the
keep-alive agent, and only while ``ZENTINELLE_GATEWAY_ENABLED`` is on.
"""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import enum
import logging
import re
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db import connection as db_connection
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from astrolift_operations.models import ZentinelleClusterGateway, ZentinelleConnection
from core.mutations import ErrorCode
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest

log = logging.getLogger(__name__)

API_PATH = "/api/zentinelle/v1/astrolift"
HTTP_TIMEOUT_SECONDS = 10

INSTALL_CREDENTIAL_PREFIX = "sk_astroinst_"
GATEWAY_CREDENTIAL_PREFIX = "sk_gateway_"
AGENT_KEY_PREFIX = "sk_agent_"
# Revocation runs in stop paths that hold a task's row lock; it retries,
# so each attempt waits less than a mutation's call does.
AGENT_KEY_REVOKE_TIMEOUT_SECONDS = 5

GATEWAY_NAMESPACE = "astrolift-system"
GATEWAY_NAME = "zentinelle-gateway"
GATEWAY_SECRET_NAME = "zentinelle-gateway-credential"
GATEWAY_SECRET_KEY = "credential"
GATEWAY_PORT = 8742
GATEWAY_CREDENTIAL_DIR = "/var/run/zentinelle"
GATEWAY_CREDENTIAL_FILE = "gateway-credential"
# The image declares no USER, but nothing in it needs root: a static binary
# listening above 1024 that writes no files.
GATEWAY_UID = 65532
GATEWAY_REPLICAS = 2

DEFAULT_ROTATION_OVERLAP_SECONDS = 600
MAX_ROTATION_OVERLAP_SECONDS = 86400

# Zentinelle's own limits on the fields a cluster registration carries.
_ENROLLMENT_CODE_MAX_LENGTH = 128
_PROVIDER = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,49}$")
_REGION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")

# First key of pg_try_advisory_xact_lock(class, cluster pk), so these keys
# never meet another feature's two-key advisory locks.
_CLUSTER_LOCK_CLASS = int.from_bytes(b"Zent", "big")


class ZentinelleConnectError(Exception):
    """A refusal a mutation answers with ``code``. ``message`` never holds a credential."""

    def __init__(
        self, code: ErrorCode, message: str, *, field: str | None = None, status: int | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        # Zentinelle's HTTP status; None when no answer came back at all.
        self.status = status


@dataclasses.dataclass(frozen=True, slots=True)
class DisconnectOutcome:
    connection: ZentinelleConnection
    warnings: list[str]
    """What disconnect could not finish: Zentinelle not told (``force``), or a
    cluster whose gateway objects are left behind."""


# ---- settings -------------------------------------------------------


def gateway_feature_enabled() -> bool:
    from constance import config

    return bool(config.ZENTINELLE_GATEWAY_ENABLED)


def _gateway_image() -> str:
    from constance import config

    image = str(config.ZENTINELLE_GATEWAY_IMAGE or "").strip()
    if not image:
        raise ZentinelleConnectError(ErrorCode.PRECONDITION, "ZENTINELLE_GATEWAY_IMAGE is empty")
    return image


def install_base_url() -> str:
    """How this Astrolift install names itself to Zentinelle."""
    return (getattr(settings, "APP_BASE_URL", "") or getattr(settings, "FRONTEND_URL", "") or "").rstrip("/")


def normalize_base_url(raw: str) -> str:
    """Validate the Zentinelle URL an admin pasted and return it without a trailing slash."""
    url = (raw or "").strip().rstrip("/")
    if not url:
        raise ZentinelleConnectError(ErrorCode.VALIDATION, "url is required", field="url")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ZentinelleConnectError(
            ErrorCode.VALIDATION,
            "url must be Zentinelle's https URL, e.g. https://zentinelle.example.com",
            field="url",
        )
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ZentinelleConnectError(
            ErrorCode.VALIDATION, "url must not carry credentials, a query or a fragment", field="url"
        )
    # Zentinelle answers with credentials over this URL; plain http only on a DEBUG install.
    if parts.scheme == "http" and not settings.DEBUG:
        raise ZentinelleConnectError(ErrorCode.VALIDATION, "url must use https", field="url")
    if len(url) > 500:
        raise ZentinelleConnectError(ErrorCode.VALIDATION, "url is longer than 500 characters", field="url")
    return url


# ---- HTTP -----------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class _Reply:
    status: int
    body: dict[str, Any]
    location: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def _call(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    credential: str = "",
    timeout: float = HTTP_TIMEOUT_SECONDS,
) -> _Reply:
    headers = {"Accept": "application/json"}
    if credential:
        headers["Authorization"] = f"Bearer {credential}"
    try:
        # Never follow a redirect: a 307/308 would re-send the payload, which
        # can carry the enrollment code, to wherever Location points.
        response = requests.request(
            method,
            url,
            json=payload,
            headers=headers,
            timeout=timeout,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise ZentinelleConnectError(
            ErrorCode.INTERNAL, f"could not reach Zentinelle at {url}: {exc}"[:500]
        ) from None
    try:
        parsed = response.json() if response.content else {}
    except ValueError:
        parsed = {}
    return _Reply(
        status=response.status_code,
        body=parsed if isinstance(parsed, dict) else {},
        location=str(response.headers.get("Location", "") or ""),
    )


def _describe(reply: _Reply) -> str:
    """The status, and Zentinelle's ``detail`` or ``error`` string. Nothing else
    from the body is repeated: the URL is the admin's, and so is what answers it."""
    for key in ("detail", "error"):
        value = reply.body.get(key)
        if isinstance(value, str) and value:
            return f"{reply.status}: {value[:200]}"
    return str(reply.status)


def _refused(reply: _Reply, action: str) -> ZentinelleConnectError:
    if 300 <= reply.status < 400:
        return ZentinelleConnectError(
            ErrorCode.INTERNAL,
            f"Zentinelle answered {action} with a redirect ({reply.status} to {reply.location or 'nowhere'}); "
            "connect with the URL it redirects to",
            status=reply.status,
        )
    if reply.status == 400:
        return ZentinelleConnectError(
            ErrorCode.VALIDATION, f"Zentinelle refused {action} ({_describe(reply)})", status=reply.status
        )
    return ZentinelleConnectError(
        ErrorCode.INTERNAL, f"Zentinelle failed {action} ({_describe(reply)})", status=reply.status
    )


def _not_connected(connection: ZentinelleConnection) -> ZentinelleConnectError:
    if connection.status == ZentinelleConnection.Status.REVOKED:
        return ZentinelleConnectError(
            ErrorCode.PRECONDITION,
            "Zentinelle no longer accepts this organization's install credential (the install was "
            "disconnected in Zentinelle). Disconnect, then connect again with a new enrollment code.",
        )
    return ZentinelleConnectError(ErrorCode.PRECONDITION, "this organization is not connected to Zentinelle")


def _require_connected(connection: ZentinelleConnection) -> None:
    if (
        connection.deleted_at is not None
        or connection.status != ZentinelleConnection.Status.CONNECTED
        or not connection.credential_ciphertext
    ):
        raise _not_connected(connection)


def _install_credential(connection: ZentinelleConnection) -> str:
    _require_connected(connection)
    sealed = EncryptedSecret(
        backend_kind=connection.credential_backend_kind,
        backend_ref=bytes(connection.credential_ciphertext),
    )
    return decrypt(sealed).decode("utf-8")


def _mark_revoked(connection: ZentinelleConnection) -> None:
    connection.status = ZentinelleConnection.Status.REVOKED
    connection.last_error = "Zentinelle refused the install credential."
    connection.save(update_fields=["status", "last_error", "updated_at", "version"])
    log.warning(
        "zentinelle: install credential of organization %s refused; connection revoked",
        connection.organization_id,
    )


def _install_call(
    connection: ZentinelleConnection,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    revoked_ok: bool = False,
    timeout: float = HTTP_TIMEOUT_SECONDS,
) -> _Reply:
    """Call Zentinelle as this install. A 401 means Zentinelle disconnected it:
    the connection is marked revoked, and the call fails unless ``revoked_ok``."""
    reply = _call(
        method,
        f"{connection.base_url}{API_PATH}{path}",
        payload=payload,
        credential=_install_credential(connection),
        timeout=timeout,
    )
    if reply.status == 401:
        _mark_revoked(connection)
        if not revoked_ok:
            raise _not_connected(connection)
    return reply


def _gateway_credential(reply: _Reply) -> tuple[str, str]:
    """The gateway credential and registration name from a clusters or rotate answer."""
    gateway = reply.body.get("gateway")
    gateway = gateway if isinstance(gateway, dict) else {}
    credential = gateway.get("credential")
    if not isinstance(credential, str) or not credential.startswith(GATEWAY_CREDENTIAL_PREFIX):
        raise ZentinelleConnectError(ErrorCode.INTERNAL, "Zentinelle's answer carried no gateway credential")
    return credential, str(gateway.get("name") or "")[:255]


# ---- manifests ------------------------------------------------------


def _labels() -> dict[str, str]:
    return {
        "app": GATEWAY_NAME,
        "app.kubernetes.io/name": GATEWAY_NAME,
        "app.kubernetes.io/part-of": "zentinelle",
        "astrolift.io/managed-by": "platform",
    }


def namespace_manifest() -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": GATEWAY_NAMESPACE, "labels": {"astrolift.io/managed-by": "platform"}},
    }


def credential_secret_manifest(credential: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": GATEWAY_SECRET_NAME, "namespace": GATEWAY_NAMESPACE, "labels": _labels()},
        "type": "Opaque",
        "data": {GATEWAY_SECRET_KEY: base64.b64encode(credential.encode("utf-8")).decode("ascii")},
    }


def gateway_manifests(*, cluster_id: str, zentinelle_url: str, image: str) -> list[dict[str, Any]]:
    """The gateway's Deployment and Service. Pure, so the shape is testable without a cluster."""
    labels = _labels()
    probe = {"httpGet": {"path": "/health", "port": "http"}}
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": GATEWAY_NAME, "namespace": GATEWAY_NAMESPACE, "labels": labels},
        "spec": {
            "replicas": GATEWAY_REPLICAS,
            "selector": {"matchLabels": {"app": GATEWAY_NAME}},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    # The gateway never calls the Kubernetes API.
                    "automountServiceAccountToken": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": GATEWAY_UID,
                        "runAsGroup": GATEWAY_UID,
                        "fsGroup": GATEWAY_UID,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "volumes": [
                        {
                            # Mounted as a directory, not with subPath, so a
                            # rotated credential reaches the running pod: the
                            # gateway rereads the file when Zentinelle refuses
                            # the old one.
                            "name": "gateway-credential",
                            "secret": {
                                "secretName": GATEWAY_SECRET_NAME,
                                "defaultMode": 0o440,
                                "items": [{"key": GATEWAY_SECRET_KEY, "path": GATEWAY_CREDENTIAL_FILE}],
                            },
                        }
                    ],
                    "containers": [
                        {
                            "name": "gateway",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "ports": [{"name": "http", "containerPort": GATEWAY_PORT, "protocol": "TCP"}],
                            "env": [
                                {"name": "ZENTINELLE_URL", "value": zentinelle_url},
                                {"name": "ZENTINELLE_CLUSTER_ID", "value": cluster_id},
                                {
                                    "name": "ZENTINELLE_GATEWAY_CREDENTIAL_FILE",
                                    "value": f"{GATEWAY_CREDENTIAL_DIR}/{GATEWAY_CREDENTIAL_FILE}",
                                },
                            ],
                            "volumeMounts": [
                                {
                                    "name": "gateway-credential",
                                    "mountPath": GATEWAY_CREDENTIAL_DIR,
                                    "readOnly": True,
                                }
                            ],
                            "readinessProbe": {**probe, "initialDelaySeconds": 2, "periodSeconds": 10},
                            "livenessProbe": {**probe, "initialDelaySeconds": 10, "periodSeconds": 20},
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "64Mi"},
                                "limits": {"memory": "256Mi"},
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                        }
                    ],
                },
            },
        },
    }
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": GATEWAY_NAME, "namespace": GATEWAY_NAMESPACE, "labels": labels},
        "spec": {
            "type": "ClusterIP",
            "selector": {"app": GATEWAY_NAME},
            "ports": [{"name": "http", "port": GATEWAY_PORT, "targetPort": "http", "protocol": "TCP"}],
        },
    }
    return [deployment, service]


def _workload_refs(*, with_secret: bool) -> list[dict[str, Any]]:
    """Kind and name of what a gateway put in the cluster, for deletion."""
    refs: list[dict[str, Any]] = [
        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": GATEWAY_NAME}},
        {"apiVersion": "v1", "kind": "Service", "metadata": {"name": GATEWAY_NAME}},
    ]
    if with_secret:
        refs.append({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": GATEWAY_SECRET_NAME}})
    return refs


# ---- cluster access -------------------------------------------------


def _redact(text: str, credential: str | None) -> str:
    """Drop a credential, raw or base64, from text bound for an error message."""
    if credential:
        text = text.replace(credential, "[redacted]")
        text = text.replace(base64.b64encode(credential.encode("utf-8")).decode("ascii"), "[redacted]")
    return text[:1000]


def _apply(cluster, manifests: list[dict[str, Any]], *, credential: str | None = None) -> None:
    from core.cluster_management import ClusterManagementError, apply_manifests_dispatch

    try:
        result = apply_manifests_dispatch(cluster=cluster, namespace=GATEWAY_NAMESPACE, manifests=manifests)
    except ClusterManagementError as exc:
        raise ZentinelleConnectError(ErrorCode.INTERNAL, _redact(str(exc), credential)) from None
    except Exception as exc:  # noqa: BLE001 - a client or auth failure reads the same to the caller
        raise ZentinelleConnectError(
            ErrorCode.INTERNAL, _redact(f"cluster {cluster.slug}: {exc}", credential)
        ) from None
    if not result.ok:
        raise ZentinelleConnectError(
            ErrorCode.INTERNAL, _redact("; ".join(str(e) for e in result.errors), credential)
        )


def _delete(cluster, refs: list[dict[str, Any]]) -> None:
    from core.cluster_management import ClusterManagementError, delete_manifests_dispatch

    try:
        result = delete_manifests_dispatch(cluster=cluster, namespace=GATEWAY_NAMESPACE, manifests=refs)
    except ClusterManagementError as exc:
        raise ZentinelleConnectError(ErrorCode.INTERNAL, _redact(str(exc), None)) from None
    except Exception as exc:  # noqa: BLE001 - a client or auth failure reads the same to the caller
        raise ZentinelleConnectError(
            ErrorCode.INTERNAL, _redact(f"cluster {cluster.slug}: {exc}", None)
        ) from None
    if not result.ok:
        raise ZentinelleConnectError(ErrorCode.INTERNAL, _redact("; ".join(result.summary()), None))


@contextlib.contextmanager
def _cluster_lock(cluster, *, wait: bool = False) -> Iterator[None]:
    """One Zentinelle operation per cluster at a time, for one transaction.

    Two overlapping registrations or rotations each mint a credential, and
    the Secret could end with the one Zentinelle expires first: a gateway
    that works for ten minutes and then refuses everything.
    """
    with transaction.atomic():
        if db_connection.vendor == "postgresql":
            with db_connection.cursor() as cursor:
                if wait:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [_CLUSTER_LOCK_CLASS, cluster.pk])
                else:
                    cursor.execute(
                        "SELECT pg_try_advisory_xact_lock(%s, %s)", [_CLUSTER_LOCK_CLASS, cluster.pk]
                    )
                    if not cursor.fetchone()[0]:
                        raise ZentinelleConnectError(
                            ErrorCode.CONFLICT,
                            "another Zentinelle operation on this cluster is in progress; retry when it finishes",
                        )
        yield


def _run_locked[T](cluster, operation: Callable[..., T], *args: Any) -> T:
    """Run ``operation`` under the cluster lock. A refusal is raised after the
    transaction commits, so what it recorded (a revoked connection, a
    gateway's ``last_error``) is kept."""
    error: ZentinelleConnectError | None = None
    outcome: T | None = None
    with _cluster_lock(cluster):
        try:
            outcome = operation(*args)
        except ZentinelleConnectError as exc:
            error = exc
    if error is not None:
        raise error
    return outcome  # type: ignore[return-value]


def _fail(
    gateway: ZentinelleClusterGateway, exc: ZentinelleConnectError, message: str
) -> ZentinelleConnectError:
    gateway.last_error = message
    gateway.save()
    return ZentinelleConnectError(exc.code, message)


def _live(gateway: ZentinelleClusterGateway) -> ZentinelleClusterGateway:
    gateway.refresh_from_db()
    if gateway.deleted_at is not None:
        raise ZentinelleConnectError(ErrorCode.NOT_FOUND, "this cluster is not registered with Zentinelle")
    gateway.connection.refresh_from_db()
    return gateway


def _deploy(gateway: ZentinelleClusterGateway) -> None:
    try:
        manifests = gateway_manifests(
            cluster_id=gateway.zentinelle_cluster_id,
            zentinelle_url=gateway.connection.base_url,
            image=_gateway_image(),
        )
        _apply(gateway.cluster, manifests)
    except ZentinelleConnectError as exc:
        raise _fail(gateway, exc, f"the gateway could not be deployed: {exc.message}") from None
    gateway.gateway_deployed = True
    gateway.last_error = ""
    gateway.save()


# ---- operations -----------------------------------------------------


def connect(*, organization, url: str, code: str, install_url: str, actor=None) -> ZentinelleConnection:
    """Exchange an enrollment code for this organization's install credential."""
    base_url = normalize_base_url(url)
    code = (code or "").strip()
    if not code or len(code) > _ENROLLMENT_CODE_MAX_LENGTH:
        raise ZentinelleConnectError(
            ErrorCode.VALIDATION,
            "enrollmentCode must be the code Zentinelle generated",
            field="enrollmentCode",
        )
    if ZentinelleConnection.objects.filter(organization=organization).exists():
        raise ZentinelleConnectError(
            ErrorCode.CONFLICT, "this organization already has a Zentinelle connection; disconnect it first"
        )

    host = urlsplit(install_url).netloc or install_url
    install = {"base_url": install_url, "name": f"{organization.name} ({host})"[:255]}
    reply = _call("POST", f"{base_url}{API_PATH}/connect", payload={"code": code, "install": install})
    if reply.status == 400 and reply.body.get("error") == "invalid_enrollment_code":
        raise ZentinelleConnectError(
            ErrorCode.VALIDATION,
            "Zentinelle refused the enrollment code: it is unknown, expired or already used. "
            "Generate a new one in Zentinelle.",
            field="enrollmentCode",
        )
    if reply.status == 404:
        raise ZentinelleConnectError(
            ErrorCode.VALIDATION,
            "no Astrolift connect endpoint at this URL: use the Zentinelle API URL, on a Zentinelle "
            "that supports connected installs",
            field="url",
        )
    if not reply.ok:
        raise _refused(reply, "the connect request")
    credential = reply.body.get("credential")
    if not isinstance(credential, str) or not credential.startswith(INSTALL_CREDENTIAL_PREFIX):
        raise ZentinelleConnectError(ErrorCode.INTERNAL, "Zentinelle's answer carried no install credential")
    answered = reply.body.get("install")
    answered = answered if isinstance(answered, dict) else {}
    tenants = answered.get("tenant_ids")

    sealed = encrypt_at_rest(credential.encode("utf-8"))
    try:
        with transaction.atomic():
            connection = ZentinelleConnection.objects.create(
                organization=organization,
                base_url=base_url,
                status=ZentinelleConnection.Status.CONNECTED,
                zentinelle_install_id=str(answered.get("id") or "")[:64],
                tenant_ids=[str(t) for t in tenants] if isinstance(tenants, list) else [],
                credential_backend_kind=sealed.backend_kind,
                credential_ciphertext=sealed.backend_ref,
                connected_at=timezone.now(),
                connected_by=actor,
            )
    except IntegrityError:
        # A concurrent connect for this org landed first. Revoke this install
        # so it is not left live in Zentinelle with nobody holding it.
        with contextlib.suppress(ZentinelleConnectError):
            _call("DELETE", f"{base_url}{API_PATH}/install", credential=credential)
        raise ZentinelleConnectError(
            ErrorCode.CONFLICT, "this organization was connected to Zentinelle by a concurrent request"
        ) from None
    log.info(
        "zentinelle: organization %s connected to %s (install %s)",
        organization.pk,
        base_url,
        connection.zentinelle_install_id,
    )
    return connection


def register_cluster(*, connection: ZentinelleConnection, cluster) -> ZentinelleClusterGateway:
    """Register ``cluster`` and write its gateway credential into the cluster.

    Registering a registered cluster again mints a fresh credential, which is
    how a lost or deleted Secret is replaced. The gateway itself is applied
    only when the cluster's gateway is on and ``ZENTINELLE_GATEWAY_ENABLED``.
    """
    return _run_locked(cluster, _register_locked, connection, cluster)


def _register_locked(connection: ZentinelleConnection, cluster) -> ZentinelleClusterGateway:
    from astrolift_clusters.models import TenantCluster

    connection.refresh_from_db()
    _require_connected(connection)
    gateway = ZentinelleClusterGateway.objects.filter(cluster=cluster).first()
    if gateway is not None and gateway.connection_id != connection.pk:
        raise ZentinelleConnectError(
            ErrorCode.CONFLICT,
            "this cluster's gateway is registered through another organization's Zentinelle connection",
            field="clusterId",
        )
    retired = (TenantCluster.Lifecycle.DECOMMISSIONING, TenantCluster.Lifecycle.DECOMMISSIONED)
    if not cluster.is_active or cluster.lifecycle in retired:
        raise ZentinelleConnectError(
            ErrorCode.PRECONDITION, "the cluster is inactive or being decommissioned", field="clusterId"
        )

    # Prove the cluster takes writes before Zentinelle mints a credential that
    # could then not be delivered.
    _apply(cluster, [namespace_manifest()])

    payload: dict[str, Any] = {"cluster_id": str(cluster.guid)}
    plugin = getattr(cluster, "provider_plugin", None)
    provider = str(getattr(plugin, "slug", "") or "")
    if _PROVIDER.match(provider):
        payload["provider"] = provider
    if _REGION.match(cluster.region or ""):
        payload["region"] = cluster.region
    reply = _install_call(connection, "POST", "/clusters", payload)
    if not reply.ok:
        raise _refused(reply, "the cluster registration")
    credential, name = _gateway_credential(reply)

    now = timezone.now()
    if gateway is None:
        gateway = ZentinelleClusterGateway(
            connection=connection,
            cluster=cluster,
            zentinelle_cluster_id=str(cluster.guid),
            registered_at=now,
        )
    else:
        gateway.credential_rotated_at = now
    gateway.gateway_name = name
    try:
        _apply(cluster, [credential_secret_manifest(credential)], credential=credential)
    except ZentinelleConnectError as exc:
        raise _fail(
            gateway,
            exc,
            f"Zentinelle registered the cluster, but its credential could not be written to it: "
            f"{exc.message}. Register it again once the cluster is reachable.",
        ) from None
    gateway.last_error = ""
    gateway.save()
    log.info("zentinelle: registered cluster %s (gateway %s)", cluster.slug, name)

    if gateway.gateway_enabled and gateway_feature_enabled():
        _deploy(gateway)
    return gateway


def set_gateway_enabled(*, gateway: ZentinelleClusterGateway, enabled: bool) -> ZentinelleClusterGateway:
    """Turn a registered cluster's gateway on (apply it) or off (remove it; the
    registration and credential Secret stay)."""
    return _run_locked(gateway.cluster, _set_enabled_locked, gateway, enabled)


def _set_enabled_locked(gateway: ZentinelleClusterGateway, enabled: bool) -> ZentinelleClusterGateway:
    gateway = _live(gateway)
    if enabled:
        if not gateway_feature_enabled():
            raise ZentinelleConnectError(
                ErrorCode.PRECONDITION,
                "the Zentinelle gateway is off for this install; turn on ZENTINELLE_GATEWAY_ENABLED first",
            )
        _require_connected(gateway.connection)
        gateway.gateway_enabled = True
        _deploy(gateway)
        return gateway

    gateway.gateway_enabled = False
    try:
        _delete(gateway.cluster, _workload_refs(with_secret=False))
    except ZentinelleConnectError as exc:
        raise _fail(gateway, exc, f"the gateway could not be removed: {exc.message}") from None
    gateway.gateway_deployed = False
    gateway.last_error = ""
    gateway.save()
    return gateway


def rotate_cluster(
    *, gateway: ZentinelleClusterGateway, overlap_seconds: int = DEFAULT_ROTATION_OVERLAP_SECONDS
) -> ZentinelleClusterGateway:
    """Mint the gateway's next credential and write it into the cluster.

    The previous credential keeps working for ``overlap_seconds``, longer
    than a Secret takes to reach a mounted volume, so a running gateway
    switches over without a refused request. 0 is for a leaked credential.
    """
    if not 0 <= overlap_seconds <= MAX_ROTATION_OVERLAP_SECONDS:
        raise ZentinelleConnectError(
            ErrorCode.VALIDATION,
            f"overlapSeconds must be between 0 and {MAX_ROTATION_OVERLAP_SECONDS}",
            field="overlapSeconds",
        )
    return _run_locked(gateway.cluster, _rotate_locked, gateway, overlap_seconds)


def _rotate_locked(gateway: ZentinelleClusterGateway, overlap_seconds: int) -> ZentinelleClusterGateway:
    gateway = _live(gateway)
    connection = gateway.connection
    _require_connected(connection)
    _apply(gateway.cluster, [namespace_manifest()])
    reply = _install_call(
        connection,
        "POST",
        f"/clusters/{gateway.zentinelle_cluster_id}/rotate",
        {"overlap_seconds": overlap_seconds},
    )
    if reply.status == 404:
        raise ZentinelleConnectError(
            ErrorCode.NOT_FOUND,
            "Zentinelle has no live registration for this cluster (it was revoked there); register it again",
            field="clusterId",
        )
    if not reply.ok:
        raise _refused(reply, "the credential rotation")
    credential, _name = _gateway_credential(reply)
    try:
        _apply(gateway.cluster, [credential_secret_manifest(credential)], credential=credential)
    except ZentinelleConnectError as exc:
        raise _fail(
            gateway,
            exc,
            f"Zentinelle rotated the credential, but the new one could not be written to the cluster: "
            f"{exc.message}. The previous one stops working {overlap_seconds} seconds after the rotation; "
            "rotate again once the cluster is reachable.",
        ) from None
    gateway.credential_rotated_at = timezone.now()
    gateway.last_error = ""
    gateway.save()
    return gateway


def unregister_cluster(*, gateway: ZentinelleClusterGateway, actor=None) -> ZentinelleClusterGateway:
    """Revoke the cluster in Zentinelle, then remove the gateway and its Secret."""
    return _run_locked(gateway.cluster, _unregister_locked, gateway, actor)


def _unregister_locked(gateway: ZentinelleClusterGateway, actor) -> ZentinelleClusterGateway:
    gateway = _live(gateway)
    connection = gateway.connection
    # A revoked install's clusters were revoked with it.
    if connection.status == ZentinelleConnection.Status.CONNECTED:
        reply = _install_call(
            connection, "DELETE", f"/clusters/{gateway.zentinelle_cluster_id}", revoked_ok=True
        )
        if not (reply.ok or reply.status in (401, 404)):
            raise _refused(reply, "the cluster revocation")
    try:
        _delete(gateway.cluster, _workload_refs(with_secret=True))
    except ZentinelleConnectError as exc:
        raise _fail(
            gateway,
            exc,
            f"Zentinelle revoked the cluster, but removing the gateway from it failed: {exc.message}. "
            "Unregister again once the cluster is reachable.",
        ) from None
    gateway.gateway_deployed = False
    gateway.last_error = ""
    gateway.save()
    gateway.soft_delete(by=actor)
    return gateway


def disconnect(*, connection: ZentinelleConnection, actor=None, force: bool = False) -> DisconnectOutcome:
    """Revoke the install in Zentinelle, then remove every gateway it registered.

    Zentinelle is told first so that the credentials are dead even where a
    cluster cannot be cleaned up. ``force`` disconnects when Zentinelle cannot
    be reached, leaving it to be disconnected there too.
    """
    connection.refresh_from_db()
    if connection.deleted_at is not None:
        raise ZentinelleConnectError(ErrorCode.NOT_FOUND, "this organization is not connected to Zentinelle")
    warnings: list[str] = []
    if connection.status == ZentinelleConnection.Status.CONNECTED:
        try:
            reply = _install_call(connection, "DELETE", "/install", revoked_ok=True)
            if not (reply.ok or reply.status == 401):
                raise _refused(reply, "the disconnect")
        except ZentinelleConnectError as exc:
            if not force:
                raise
            warnings.append(
                f"Zentinelle was not told ({exc.message}); disconnect this install in Zentinelle "
                "(Settings > Astrolift) to revoke its credentials."
            )

    for gateway in ZentinelleClusterGateway.objects.filter(connection=connection).select_related("cluster"):
        cluster = gateway.cluster
        with _cluster_lock(cluster, wait=True):
            gateway.refresh_from_db()
            if gateway.deleted_at is not None:
                continue
            try:
                _delete(cluster, _workload_refs(with_secret=True))
            except ZentinelleConnectError as exc:
                gateway.last_error = f"the gateway could not be removed: {exc.message}"
                gateway.save()
                warnings.append(
                    f"cluster {cluster.slug}: {exc.message}. Remove Deployment and Service {GATEWAY_NAME} "
                    f"and Secret {GATEWAY_SECRET_NAME} from {GATEWAY_NAMESPACE} by hand."
                )
            gateway.soft_delete(by=actor)

    connection.status = ZentinelleConnection.Status.DISCONNECTED
    connection.disconnected_at = timezone.now()
    connection.credential_ciphertext = b""
    connection.last_error = "; ".join(warnings)[:2000]
    connection.save()
    connection.soft_delete(by=actor)
    log.info(
        "zentinelle: organization %s disconnected from %s", connection.organization_id, connection.base_url
    )
    return DisconnectOutcome(connection=connection, warnings=warnings)


# ---- per-run agent keys (#1851) ------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class AgentKey:
    """A key Zentinelle minted for one agent run. ``api_key`` belongs in the
    run's Secret and nowhere else, so it stays out of ``repr``."""

    agent_id: str
    api_key: str = dataclasses.field(repr=False)
    expires_at: datetime | None
    lifetime_ends_at: datetime | None
    """When Zentinelle stops honouring renewals of the key (its lifetime cap)."""


def _agent_times(reply: _Reply) -> tuple[datetime | None, datetime | None]:
    """``expires_at`` and ``lifetime_ends_at`` from an agent key answer."""
    agent = reply.body.get("agent")
    agent = agent if isinstance(agent, dict) else {}
    times = []
    for name in ("expires_at", "lifetime_ends_at"):
        raw = agent.get(name)
        times.append(parse_datetime(raw) if isinstance(raw, str) else None)
    return times[0], times[1]


def _agent_key_refusal(reply: _Reply, agent_id: str, action: str) -> ZentinelleConnectError:
    """What Zentinelle's per-run key endpoints refuse with, in words an operator acts on."""
    error = reply.body.get("error")
    if reply.status == 409 and error == "agent_suspended":
        return ZentinelleConnectError(
            ErrorCode.PRECONDITION,
            f"agent {agent_id} was stopped in Zentinelle by someone other than this install (an "
            "administrator, the kill switch, the operator API or the agent itself), and it stays stopped",
            status=reply.status,
        )
    if reply.status == 409 and error == "agent_key_expired":
        return ZentinelleConnectError(
            ErrorCode.PRECONDITION,
            f"the key of agent {agent_id} expired; only a new mint replaces it",
            status=reply.status,
        )
    return _refused(reply, action)


def mint_agent_key(
    *,
    connection: ZentinelleConnection,
    agent_id: str,
    ttl_seconds: int,
    name: str = "",
    deployment_id: str = "",
) -> AgentKey:
    """Mint the key of ``agent_id`` for one run, as this organization's install.

    Minting again for the same agent replaces its key, so a retried spawn or a
    restarted box gets a fresh one and the old one stops working. The key's
    ``expires_at`` is Zentinelle's: it may be earlier than ``ttl_seconds``
    asked for, when its lifetime cap comes first.
    """
    _require_connected(connection)
    payload = {
        "agent_id": agent_id,
        "ttl_seconds": ttl_seconds,
        "name": name[:255],
        "deployment_id": deployment_id[:255],
    }
    reply = _install_call(connection, "POST", "/agents", payload)
    if reply.status == 404:
        raise ZentinelleConnectError(
            ErrorCode.PRECONDITION,
            "this Zentinelle cannot mint per-run agent keys (no /astrolift/agents endpoint, "
            "calliopeai/zentinelle#400); upgrade it",
            status=reply.status,
        )
    if not reply.ok:
        raise _agent_key_refusal(reply, agent_id, "the agent key request")
    api_key = reply.body.get("api_key")
    if not isinstance(api_key, str) or not api_key.startswith(AGENT_KEY_PREFIX):
        raise ZentinelleConnectError(
            ErrorCode.INTERNAL, "Zentinelle's answer carried no agent key", status=reply.status
        )
    expires_at, lifetime_ends_at = _agent_times(reply)
    return AgentKey(
        agent_id=agent_id, api_key=api_key, expires_at=expires_at, lifetime_ends_at=lifetime_ends_at
    )


def renew_agent_key(
    *, connection: ZentinelleConnection, agent_id: str, ttl_seconds: int
) -> tuple[datetime | None, datetime | None]:
    """Let the live key of ``agent_id`` work for ``ttl_seconds`` more, within its lifetime.

    Returns its new ``expires_at`` and its ``lifetime_ends_at``.
    """
    _require_connected(connection)
    reply = _install_call(connection, "POST", f"/agents/{agent_id}/renew", {"ttl_seconds": ttl_seconds})
    if not reply.ok:
        raise _agent_key_refusal(reply, agent_id, "the agent key renewal")
    return _agent_times(reply)


class Revocation(enum.StrEnum):
    """What Zentinelle answered a revocation with."""

    REVOKED = "revoked"
    """Terminated now, or it already was."""
    UNKNOWN_AGENT = "unknown_agent"
    """This install minted no such agent, as far as Zentinelle knows (404)."""
    INSTALL_REFUSED = "install_refused"
    """Zentinelle no longer accepts this install (401)."""


def revoke_agent_key(*, connection: ZentinelleConnection, agent_id: str) -> Revocation:
    """Terminate ``agent_id`` in Zentinelle, as the install that minted it."""
    _require_connected(connection)
    reply = _install_call(
        connection,
        "DELETE",
        f"/agents/{agent_id}",
        revoked_ok=True,
        timeout=AGENT_KEY_REVOKE_TIMEOUT_SECONDS,
    )
    if reply.ok:
        return Revocation.REVOKED
    if reply.status == 404:
        return Revocation.UNKNOWN_AGENT
    if reply.status == 401:
        return Revocation.INSTALL_REFUSED
    raise _refused(reply, "the agent key revocation")


def fenced_agents_reach_gateway(cluster) -> bool:
    """Whether the agent network fence on ``cluster`` opens egress to its gateway (#1850)."""
    cluster_pk = getattr(cluster, "pk", None)
    if cluster_pk is None or not gateway_feature_enabled():
        return False
    return ZentinelleClusterGateway.objects.filter(cluster_id=cluster_pk, gateway_enabled=True).exists()
