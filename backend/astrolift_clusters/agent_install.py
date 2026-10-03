"""Server-owned, reviewed keep-alive installation with staged credentials."""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import re
import secrets
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from astrolift_clusters.models import ClusterAgentInstall, ProviderPlugin, TenantCluster
from astrolift_clusters.scopes import cluster_org_scope
from astrolift_identity.abac import RequestAttributes, current_attributes, request_attributes
from astrolift_identity.api_tokens import (
    get_current_api_token,
    reset_current_api_token,
    session_may_act_in,
    set_current_api_token,
    token_scope_allows_permission,
    with_active_org_member,
)
from astrolift_identity.models import ApiToken, Organization
from core.current_credential import current_dispatch_credential
from core.permissions import Permission, PermissionDenied, check_permission, check_platform_operator
from core.secrets import EncryptedSecret, decrypt, encrypt_at_rest
from core.tenancy import TenantContext, get_current_tenant, tenant_context

ACTIVE_STATUSES = ["queued", "installing", "awaiting_heartbeat", "uncertain"]


class AgentInstallError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _digest(value):
    return hmac.new(
        settings.SECRET_KEY.encode(),
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(),
        hashlib.sha256,
    ).hexdigest()


def heartbeat_origin():
    base = (getattr(settings, "APP_BASE_URL", "") or "").rstrip("/")
    parsed = urlsplit(base)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise AgentInstallError(
            "PRECONDITION", "Configure an absolute control-plane APP_BASE_URL before installing the agent"
        )
    return base


def _source(cluster, plugin):
    from core.cluster_management import AGENT_IMAGE

    return _digest(
        [
            str(cluster.guid),
            cluster.version,
            cluster.updated_at,
            cluster.organization_id,
            cluster.slug,
            cluster.provider_plugin_id,
            cluster.region,
            cluster.endpoint,
            cluster.ca_cert,
            cluster.auth_method,
            cluster.auth_config,
            cluster.provider_config,
            cluster.cloud_account_id,
            cluster.cloud_account_verified_at,
            cluster.is_active,
            cluster.deleted_at,
            cluster.heartbeat_interval_seconds,
            cluster.agent_key_hash,
            cluster.agent_secret_name,
            cluster.agent_secret_uid,
            cluster.agent_deployment_uid,
            cluster.agent_deployment_name,
            str(plugin.guid),
            plugin.version,
            plugin.updated_at,
            plugin.slug,
            plugin.is_enabled,
            plugin.deleted_at,
            heartbeat_origin(),
            AGENT_IMAGE,
        ]
    )


def _ceiling():
    token = get_current_api_token()
    attrs = current_attributes()
    authenticated_at = attrs.session_authenticated_at() if attrs else None
    factors = attrs.session_factors() if attrs else None
    return {
        "token_id": token.pk if token else None,
        "token_guid": str(token.guid) if token else None,
        "organization_id": token.organization_id if token else None,
        "team_id": token.team_id if token else None,
        "scopes": list(token.scopes or []) if token else [],
        "client_ip": attrs.client_ip if attrs else None,
        "authenticated_at": authenticated_at.isoformat() if authenticated_at else None,
        "auth_factors": sorted(factors) if factors is not None else None,
    }


def _caller_gate(cluster, user):
    scope = cluster_org_scope(Permission.CLUSTER_MANAGE)({"cluster_id": str(cluster.guid)})
    with current_dispatch_credential(Permission.CLUSTER_MANAGE):
        token = get_current_api_token()
        if (
            token is not None
            and not with_active_org_member(
                ApiToken.objects.filter(pk=token.pk), user="user", organization="organization"
            ).exists()
        ):
            raise AgentInstallError(
                "PERMISSION_DENIED",
                "The dispatch credential owner is no longer an active organization member",
            )
        check_permission(Permission.CLUSTER_MANAGE, scope=scope)
        if cluster.organization_id is None:
            check_platform_operator(user, gate=Permission.CLUSTER_MANAGE)


def _visible_cluster(cluster_id, *, lock=False):
    tenant = get_current_tenant()
    if tenant is None or tenant.organization_id is None or tenant.actor_user_id is None:
        raise AgentInstallError("PERMISSION_DENIED", "An authenticated organization actor is required")
    query = TenantCluster.objects.filter(
        Q(organization_id=tenant.organization_id) | Q(organization_id__isnull=True), guid=str(cluster_id)
    )
    if lock:
        query = query.select_for_update()
    cluster = query.first()
    if cluster is None:
        raise AgentInstallError("NOT_FOUND", "Cluster not found")
    user = get_user_model().objects.filter(pk=tenant.actor_user_id, is_active=True).first()
    if user is None:
        raise AgentInstallError("PERMISSION_DENIED", "The installation actor is unavailable")
    _caller_gate(cluster, user)
    return cluster, user


def _review_source(cluster, plugin):
    from core.cluster_management import AGENT_IMAGE

    # Public proof binds tracked source revisions; it deliberately contains no
    # credential values or credential-derived hashes. Supported source writers
    # advance these persisted markers under their canonical row locks.
    return _digest(
        [
            str(cluster.guid),
            cluster.version,
            cluster.updated_at,
            cluster.provider_plugin_id,
            str(plugin.guid),
            plugin.version,
            plugin.updated_at,
            heartbeat_origin(),
            AGENT_IMAGE,
        ]
    )


def review_install(cluster_id):
    with transaction.atomic(), current_dispatch_credential(Permission.CLUSTER_MANAGE):
        cluster, _user = _visible_cluster(cluster_id, lock=True)
        if not cluster.is_active:
            raise AgentInstallError("PRECONDITION", "Cluster is inactive")
        plugin = (
            ProviderPlugin.objects.select_for_update()
            .filter(pk=cluster.provider_plugin_id, is_enabled=True)
            .first()
        )
        if plugin is None:
            raise AgentInstallError("PRECONDITION", "Cluster provider is unavailable")
        return cluster, _review_source(cluster, plugin)


def installation_busy(cluster):
    return ClusterAgentInstall.objects.filter(tenant_cluster=cluster, status__in=ACTIVE_STATUSES).exists()


def reserve_install(*, cluster_id, expected_version, expected_source, request_id, interval_seconds):
    try:
        request_uuid = UUID(request_id)
        if str(request_uuid) != request_id or not request_uuid.int:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise AgentInstallError("VALIDATION", "requestId must be a canonical nonzero UUID") from None
    tenant = get_current_tenant()
    with transaction.atomic(), current_dispatch_credential(Permission.CLUSTER_MANAGE):
        cluster, user = _visible_cluster(cluster_id, lock=True)
        assert tenant is not None and user is not None
        token = get_current_api_token()
        requested = [
            str(cluster.guid),
            expected_version,
            expected_source,
            interval_seconds,
            token.pk if token else None,
        ]
        body_digest = _digest(requested)
        existing = (
            ClusterAgentInstall.all_objects.select_for_update()
            .filter(organization_id=tenant.organization_id, actor_id=user.pk, request_id=request_uuid)
            .first()
        )
        if existing:
            if existing.deleted_at or existing.request_digest != body_digest:
                raise AgentInstallError(
                    "PRECONDITION", "requestId already belongs to a different or deleted installation"
                )
            return existing
        if not cluster.is_active or cluster.version != expected_version:
            raise AgentInstallError("PRECONDITION", "Cluster changed or is inactive; review it again")
        plugin = (
            ProviderPlugin.objects.select_for_update()
            .filter(pk=cluster.provider_plugin_id, is_enabled=True)
            .first()
        )
        if plugin is None:
            raise AgentInstallError("PRECONDITION", "Cluster provider is unavailable")
        if (
            not isinstance(expected_source, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_source)
            or not hmac.compare_digest(_review_source(cluster, plugin), expected_source)
        ):
            raise AgentInstallError(
                "PRECONDITION", "Reviewed cluster or provider source changed; review it again"
            )
        _refuse_invalid_pending(cluster, plugin)
        if installation_busy(cluster):
            raise AgentInstallError(
                "PRECONDITION", "Another installation is still pending; resume its exact request"
            )
        source = _source(cluster, plugin)
        raw = secrets.token_hex(32)
        encrypted = encrypt_at_rest(raw.encode())
        try:
            # A different cluster/plugin lock may concurrently admit this actor's
            # UUID. Keep the outer transaction usable and refuse that retarget.
            with transaction.atomic():
                return ClusterAgentInstall.objects.create(
                    tenant_cluster=cluster,
                    organization_id=tenant.organization_id,
                    actor=user,
                    request_id=request_uuid,
                    request_digest=body_digest,
                    expected_version=expected_version,
                    source_digest=source,
                    credential_ceiling=_ceiling(),
                    credential_hash=hashlib.sha256(raw.encode()).hexdigest(),
                    credential_backend_kind=encrypted.backend_kind,
                    credential_ciphertext=encrypted.backend_ref,
                    interval_seconds=cluster.heartbeat_interval_seconds
                    if interval_seconds is None
                    else max(5, min(interval_seconds, 60)),
                    created_by=user,
                    updated_by=user,
                )
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.__cause__, "diag", None), "constraint_name", None)
            if constraint not in {"agent_install_actor_request_unique", "agent_install_one_live_attempt"}:
                raise
            raise AgentInstallError(
                "PRECONDITION",
                "requestId or cluster already belongs to another installation; resume its exact request",
            ) from None


@contextmanager
def _actor(row):
    ceiling = row.credential_ceiling
    user = get_user_model().objects.filter(pk=row.actor_id, is_active=True).first()
    if user is None or not Organization.objects.filter(pk=row.organization_id).exists():
        raise AgentInstallError("PERMISSION_DENIED", "The installation actor or organization is unavailable")
    token = None
    if ceiling["token_id"] is None and not session_may_act_in(user, row.organization_id):
        raise AgentInstallError(
            "PERMISSION_DENIED",
            "The original session actor no longer belongs to the installation organization",
        )
    if ceiling["token_id"] is not None:
        token = (
            ApiToken.objects.filter(
                pk=ceiling["token_id"],
                guid=ceiling["token_guid"],
                user_id=user.pk,
                organization_id=row.organization_id,
                team_id=ceiling["team_id"],
                is_revoked=False,
            )
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
            .first()
        )
        if (
            token is None
            or not with_active_org_member(
                ApiToken.objects.filter(pk=token.pk), user="user", organization="organization"
            ).exists()
            or not token_scope_allows_permission(
                SimpleNamespace(scopes=ceiling["scopes"]), Permission.CLUSTER_MANAGE.value
            )
        ):
            raise AgentInstallError(
                "PERMISSION_DENIED",
                "The original installation credential is unavailable or does not permit this action",
            )
    auth_time = datetime.fromisoformat(ceiling["authenticated_at"]) if ceiling["authenticated_at"] else None
    attrs = RequestAttributes(
        actor_user_id=user.pk,
        client_ip=ceiling["client_ip"],
        authenticated_at=auth_time,
        auth_factors=frozenset(ceiling["auth_factors"]) if ceiling["auth_factors"] is not None else None,
    )
    state = set_current_api_token(token)
    try:
        with (
            tenant_context(TenantContext(organization_id=row.organization_id, actor_user_id=user.pk)),
            request_attributes(attrs),
        ):
            yield user
    finally:
        reset_current_api_token(state)


def _authorize(row, cluster, plugin):
    cluster.refresh_from_db()
    plugin.refresh_from_db()
    with _actor(row) as user:
        _caller_gate(cluster, user)
        if cluster.organization_id is None and row.credential_ceiling["token_id"] is not None:
            if "admin" not in row.credential_ceiling["scopes"]:
                raise AgentInstallError(
                    "PERMISSION_DENIED", "The original credential does not cover shared cluster installation"
                )
    if (
        not cluster.is_active
        or cluster.deleted_at
        or not plugin.is_enabled
        or plugin.deleted_at
        or _source(cluster, plugin)
        != (row.activation_source_digest if row.status == "succeeded" else row.source_digest)
    ):
        raise AgentInstallError(
            "PRECONDITION", "Reviewed cluster or provider source changed; installation refused"
        )


def _refuse_invalid_pending(cluster, plugin):
    # A currently authorized, freshly reviewed admission can release a withdrawn
    # operation's metadata lease. It never revives the original credential ceiling
    # or changes provider objects/current agent authentication.
    for pending in ClusterAgentInstall.objects.select_for_update().filter(
        tenant_cluster=cluster, status__in=ACTIVE_STATUSES
    ):
        try:
            _authorize(pending, cluster, plugin)
        except (AgentInstallError, PermissionDenied):
            pending.status = "refused"
            pending.error_code = "PRECONDITION"
            pending.error_message = "Original installation authority or reviewed source was withdrawn; current agent remains active"
            pending.save()


def _locked(row_id):
    # Cluster-before-operation ordering also governs heartbeat activation and replay.
    candidate = (
        ClusterAgentInstall.objects.filter(pk=row_id).values("tenant_cluster_id", "organization_id").first()
    )
    if candidate is None:
        raise AgentInstallError("NOT_FOUND", "Installation not found")
    cluster = TenantCluster.all_objects.select_for_update().get(pk=candidate["tenant_cluster_id"])
    plugin = ProviderPlugin.all_objects.select_for_update().get(pk=cluster.provider_plugin_id)
    row = ClusterAgentInstall.objects.select_for_update().get(pk=row_id)
    return row, cluster, plugin


def read_install(install_id):
    tenant = get_current_tenant()
    if tenant is None:
        raise AgentInstallError("PERMISSION_DENIED", "An authenticated organization actor is required")
    row = ClusterAgentInstall.objects.filter(
        guid=str(install_id), organization_id=tenant.organization_id, actor_id=tenant.actor_user_id
    ).first()
    if row is None:
        raise AgentInstallError("NOT_FOUND", "Installation not found")
    with transaction.atomic(), current_dispatch_credential(Permission.CLUSTER_MANAGE):
        cluster, _ = _visible_cluster(row.tenant_cluster.guid, lock=True)
        with _actor(row) as user:
            _caller_gate(cluster, user)
        return row


def dispatch_install(row):
    from astrolift_workflows.client import (
        describe_workflow_instance,
        recover_workflow_once,
        start_workflow_once,
    )

    with transaction.atomic(), current_dispatch_credential(Permission.CLUSTER_MANAGE):
        row, cluster, plugin = _locked(row.pk)
        _visible_cluster(cluster.guid)
        with _actor(row) as user:
            _caller_gate(cluster, user)
        if row.status == "succeeded":
            retire_previous_deployment(row.pk)
            row.refresh_from_db()
            return row
        if row.status == "refused":
            return row
        try:
            _authorize(row, cluster, plugin)
        except (AgentInstallError, PermissionDenied):
            row.status = "refused"
            row.error_code = "PRECONDITION"
            row.error_message = (
                "Installation authority or reviewed source changed; current credential remains active"
            )
            row.save()
            return row
        if row.status == "awaiting_heartbeat":
            return row
        args = [row.pk, row.generation]
        try:
            handle = recover_workflow_once("InstallClusterAgentWorkflow", args, workflow_id=row.workflow_id)
            if handle is not None:
                if row.workflow_run_id and row.workflow_run_id != handle.run_id:
                    raise RuntimeError("Execution changed")
                description = describe_workflow_instance(row.workflow_id, run_id=handle.run_id)
                if (
                    description is None
                    or description["run_id"] != handle.run_id
                    or description["workflow_type"] != "InstallClusterAgentWorkflow"
                ):
                    raise RuntimeError("Execution unavailable")
                if description["status"] == "RUNNING":
                    row.workflow_run_id = handle.run_id
                    row.save()
                    return row
                if not description.get("closed_at") or description["status"] not in {
                    "COMPLETED",
                    "FAILED",
                    "TIMED_OUT",
                    "CANCELED",
                    "TERMINATED",
                }:
                    raise RuntimeError("Execution outcome unknown")
                row.generation += 1
                row.workflow_run_id = ""
                row.status = "queued"
                row.save()
                args = [row.pk, row.generation]
            elif row.workflow_run_id:
                raise RuntimeError("Recorded execution is unavailable")
            handle = start_workflow_once("InstallClusterAgentWorkflow", args, workflow_id=row.workflow_id)
            row.workflow_run_id = handle.run_id
            row.error_code = ""
            row.error_message = ""
        except Exception:
            row.status = "uncertain"
            row.error_code = "DISPATCH_UNCERTAIN"
            row.error_message = "Workflow dispatch could not be confirmed; resume the same request"
        row.save()
        return row


class KubernetesInstallPort:
    """Use the installed cloud driver's exact auth client; never instrument Secret bodies."""

    def __init__(self, cluster):
        from _sdk.k8s_dynamic_client import KubernetesDynamicClient

        from core.cluster_management import _driver_for_cluster

        try:
            driver = _driver_for_cluster(cluster)
            factory = getattr(driver, "_k8s", None)
            client = factory(cluster.slug) if callable(factory) else None
        except Exception:
            # Driver construction/discovery can fail before the HTTP boundary;
            # its exception may contain registered credential/config values.
            raise AgentInstallError(
                "PROVIDER_UNCERTAIN", "Kubernetes installation transport could not be confirmed"
            ) from None
        if not isinstance(client, KubernetesDynamicClient):
            raise AgentInstallError(
                "PRECONDITION",
                "Provider does not expose the required conditional Kubernetes installation transport",
            )
        # The generated Kubernetes client can log complete request bodies when
        # debug is enabled. This transport must keep Secret bytes out of logs.
        client._api_client.configuration.debug = False
        self.client = client

    def call(self, method, manifest=None, *, kind=None, name=None, namespace=None):
        from _sdk.k8s_dynamic_client import split_kind
        from kubernetes.dynamic.exceptions import ConflictError, NotFoundError

        try:
            self.client._refresh_token()
            api_version, kind_name = split_kind(kind or manifest["kind"])
            resource = self.client._resource_for(api_version, kind_name)
            kwargs = {"namespace": namespace if resource.namespaced else None, "_request_timeout": (5, 30)}
            if method == "get":
                return self.client._to_dict(resource.get(name=name, **kwargs))
            if method == "create":
                return self.client._to_dict(resource.create(body=manifest, **kwargs))
            if method == "delete":
                return self.client._to_dict(resource.delete(name=name, body=manifest, **kwargs))
            return self.client._to_dict(
                resource.server_side_apply(
                    body=manifest, field_manager="astrolift-agent-install", force_conflicts=False, **kwargs
                )
            )
        except NotFoundError:
            if method in {"get", "delete"}:
                return None
            raise AgentInstallError(
                "PROVIDER_UNCERTAIN", "Kubernetes installation write could not be confirmed"
            ) from None
        except ConflictError:
            raise AgentInstallError(
                "IDENTITY_CONFLICT", "Kubernetes object changed or its name is occupied; installation refused"
            ) from None
        except AgentInstallError:
            raise
        except Exception:
            raise AgentInstallError(
                "PROVIDER_UNCERTAIN",
                "Kubernetes installation request could not be confirmed; resume the same request",
            ) from None


def _uid(obj):
    metadata = (obj or {}).get("metadata") or {}
    if any(not isinstance(metadata.get(key), str) or not metadata[key] for key in ("uid", "resourceVersion")):
        raise AgentInstallError("PRECONDITION", "Kubernetes object identity could not be confirmed")
    return {"uid": metadata["uid"], "resourceVersion": metadata["resourceVersion"]}


def _secret_matches(row, cluster, obj):
    metadata = (obj or {}).get("metadata") or {}
    labels = metadata.get("labels") or {}
    data = (obj or {}).get("data") or {}
    try:
        raw = base64.b64decode(data.get("agent_key", ""), validate=True)
        url = base64.b64decode(data.get("heartbeat_url", ""), validate=True).decode()
    except (ValueError, TypeError, UnicodeError):
        return False
    return (
        metadata.get("name") == row.secret_name
        and metadata.get("namespace") == "astrolift-system"
        and set(data) == {"agent_key", "heartbeat_url"}
        and labels.get("astrolift.io/cluster-guid") == str(cluster.guid)
        and labels.get("astrolift.io/install-guid") == str(row.guid)
        and obj.get("immutable") is True
        and obj.get("type") == "Opaque"
        and hashlib.sha256(raw).hexdigest() == row.credential_hash
        and url == f"{heartbeat_origin()}/api/clusters/v1/{cluster.guid}/heartbeat/"
    )


def _checkpoint(row, cluster, plugin):
    _authorize(row, cluster, plugin)
    row.save()


def _desired_manifest(row, cluster, manifest):
    desired = copy.deepcopy(manifest)
    desired["metadata"].setdefault("labels", {})["astrolift.io/cluster-guid"] = str(cluster.guid)
    desired["metadata"].setdefault("annotations", {})["astrolift.io/install-guid"] = str(row.guid)
    if desired["kind"] == "Deployment":
        desired["metadata"]["name"] = row.deployment_name
        desired["spec"]["selector"]["matchLabels"] = {"app": row.deployment_name}
        desired["spec"]["template"]["metadata"]["labels"]["app"] = row.deployment_name
        desired["spec"]["template"]["metadata"].setdefault("annotations", {})["astrolift.io/install-guid"] = (
            str(row.guid)
        )
        for env in desired["spec"]["template"]["spec"]["containers"][0]["env"]:
            if "valueFrom" in env:
                env["valueFrom"]["secretKeyRef"]["name"] = row.secret_name
            if env["name"] == "INTERVAL_SECONDS":
                env["value"] = str(row.interval_seconds)
    return desired


def _resource_identity(row, cluster, desired, current):
    key = f"{desired['kind']}/{desired['metadata']['name']}"
    recorded = row.manifest_identities.get(key)
    if current is None:
        if recorded:
            raise AgentInstallError(
                "IDENTITY_CONFLICT", "Recorded agent resource disappeared; replacement refused"
            )
        return None
    observed = _uid(current)
    if desired["kind"] == "Deployment" and (
        (current["metadata"].get("annotations") or {}).get("astrolift.io/install-guid") != str(row.guid)
        or not _deployment_matches(row, cluster, current)
    ):
        raise AgentInstallError(
            "IDENTITY_CONFLICT", "Candidate agent Deployment is foreign or changed; adoption refused"
        )
    if recorded and recorded["uid"] != observed["uid"]:
        raise AgentInstallError("IDENTITY_CONFLICT", "Recorded agent resource was replaced; adoption refused")
    labels = current["metadata"].get("labels") or {}
    if (
        labels.get("app") != "astrolift-agent"
        or labels.get("astrolift.io/managed-by") != "platform"
        or labels.get("astrolift.io/cluster-guid", str(cluster.guid)) != str(cluster.guid)
    ):
        raise AgentInstallError("IDENTITY_CONFLICT", "Existing agent resource is foreign; adoption refused")
    return observed


def _verify_namespace(row, cluster, port):
    namespace = port.call("get", kind="Namespace", name="astrolift-system")
    recorded = row.manifest_identities.get("Namespace/astrolift-system") or {}
    if not _namespace_owned(cluster, namespace) or _uid(namespace)["uid"] != recorded.get("uid"):
        raise AgentInstallError("IDENTITY_CONFLICT", "Recorded agent Namespace changed; installation refused")


def _namespace_owned(cluster, namespace):
    if not namespace:
        return False
    metadata = namespace.get("metadata") or {}
    labels = metadata.get("labels") or {}
    return (
        metadata.get("name") == "astrolift-system"
        and labels.get("astrolift.io/managed-by") in {"platform", "astrolift-control-plane"}
        and labels.get("astrolift.io/cluster-guid", str(cluster.guid)) == str(cluster.guid)
    )


@contextmanager
def _stage(row_id, generation):
    # Each confirmed identity commits before the next provider effect. Holding
    # the canonical source locks freezes code-owned source writes within a stage.
    with transaction.atomic():
        row, cluster, plugin = _locked(row_id)
        if row.generation != generation or row.status in {"succeeded", "refused", "awaiting_heartbeat"}:
            raise AgentInstallError("STALE_ATTEMPT", "Installation attempt is no longer current")
        _authorize(row, cluster, plugin)
        row.status = "installing"
        row.save()
        port = KubernetesInstallPort(cluster)
        recorded = row.manifest_identities.get("Namespace/astrolift-system")
        if recorded:
            namespace = port.call("get", kind="Namespace", name="astrolift-system")
            if not _namespace_owned(cluster, namespace) or _uid(namespace)["uid"] != recorded["uid"]:
                raise AgentInstallError(
                    "IDENTITY_CONFLICT", "Recorded agent Namespace changed; installation refused"
                )
        yield row, cluster, plugin, port


def install_attempt(row_id, generation, *, execution=None):
    if execution is not None:
        with transaction.atomic():
            row, _cluster, _plugin = _locked(row_id)
            workflow_id, run_id, workflow_type = execution
            if (
                row.generation != generation
                or workflow_id != row.workflow_id
                or workflow_type != "InstallClusterAgentWorkflow"
                or not run_id
                or row.workflow_run_id
                and row.workflow_run_id != run_id
            ):
                return "refused"
            # A lost dispatch reply leaves the run unknown. The trusted worker
            # binds its own exact never-reused execution before any cloud effect.
            row.workflow_run_id = run_id
            row.save()
    from core.cluster_management import build_agent_manifests

    try:
        with _stage(row_id, generation) as (row, cluster, plugin, port):
            namespace = port.call("get", kind="Namespace", name="astrolift-system")
            if namespace is None:
                _authorize(row, cluster, plugin)
                port.call(
                    "create",
                    {
                        "apiVersion": "v1",
                        "kind": "Namespace",
                        "metadata": {
                            "name": "astrolift-system",
                            "labels": {
                                "astrolift.io/managed-by": "platform",
                                "astrolift.io/cluster-guid": str(cluster.guid),
                            },
                        },
                    },
                )
            if namespace is None:
                namespace = port.call("get", kind="Namespace", name="astrolift-system")
            if not _namespace_owned(cluster, namespace):
                raise AgentInstallError(
                    "IDENTITY_CONFLICT", "Existing agent Namespace is foreign; adoption refused"
                )
            row.manifest_identities["Namespace/astrolift-system"] = _uid(namespace)
            previous_name = cluster.agent_deployment_name or "astrolift-agent"
            previous = port.call("get", kind="Deployment", name=previous_name, namespace="astrolift-system")
            if previous is not None:
                labels = previous["metadata"].get("labels") or {}
                identity = _uid(previous)
                if (
                    labels.get("app") != "astrolift-agent"
                    or labels.get("astrolift.io/managed-by") != "platform"
                    or labels.get("astrolift.io/cluster-guid", str(cluster.guid)) != str(cluster.guid)
                    or cluster.agent_deployment_uid
                    and cluster.agent_deployment_uid != identity["uid"]
                ):
                    raise AgentInstallError(
                        "IDENTITY_CONFLICT",
                        "Current agent Deployment is foreign or replaced; installation refused",
                    )
                if row.previous_deployment_uid and row.previous_deployment_uid != identity["uid"]:
                    raise AgentInstallError(
                        "IDENTITY_CONFLICT",
                        "Previously observed agent Deployment was replaced; installation refused",
                    )
                row.previous_deployment_name, row.previous_deployment_uid = previous_name, identity["uid"]
            elif cluster.agent_deployment_uid or row.previous_deployment_uid:
                raise AgentInstallError(
                    "IDENTITY_CONFLICT",
                    "Previously observed agent Deployment disappeared; installation refused",
                )
            _checkpoint(row, cluster, plugin)
        with _stage(row_id, generation) as (row, cluster, plugin, port):
            secret = port.call("get", kind="Secret", name=row.secret_name, namespace="astrolift-system")
            if secret is None:
                if row.secret_uid:
                    raise AgentInstallError(
                        "IDENTITY_CONFLICT", "Recorded agent Secret disappeared; replacement refused"
                    )
                raw = decrypt(EncryptedSecret(row.credential_backend_kind, bytes(row.credential_ciphertext)))
                desired = {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {
                        "name": row.secret_name,
                        "namespace": "astrolift-system",
                        "labels": {
                            "astrolift.io/managed-by": "platform",
                            "astrolift.io/cluster-guid": str(cluster.guid),
                            "astrolift.io/install-guid": str(row.guid),
                        },
                    },
                    "type": "Opaque",
                    "immutable": True,
                    "data": {
                        "agent_key": base64.b64encode(raw).decode(),
                        "heartbeat_url": base64.b64encode(
                            f"{heartbeat_origin()}/api/clusters/v1/{cluster.guid}/heartbeat/".encode()
                        ).decode(),
                    },
                }
                _authorize(row, cluster, plugin)
                secret = port.call("create", desired, namespace="astrolift-system")
            if not _secret_matches(row, cluster, secret):
                raise AgentInstallError(
                    "IDENTITY_CONFLICT", "Agent Secret does not belong to this installation; adoption refused"
                )
            identity = _uid(secret)
            if row.secret_uid and row.secret_uid != identity["uid"]:
                raise AgentInstallError(
                    "IDENTITY_CONFLICT", "Recorded agent Secret was replaced; adoption refused"
                )
            row.secret_uid = identity["uid"]
            row.secret_resource_version = identity["resourceVersion"]
            _checkpoint(row, cluster, plugin)
            manifests = build_agent_manifests(cluster)
        for manifest in manifests:
            if manifest["kind"] == "Namespace":
                continue
            # Persist an observed existing UID before trying a conditional write.
            with _stage(row_id, generation) as (row, cluster, plugin, port):
                desired = _desired_manifest(row, cluster, manifest)
                kind, metadata = desired["kind"], desired["metadata"]
                current = port.call(
                    "get", kind=kind, name=metadata["name"], namespace=metadata.get("namespace")
                )
                observed = _resource_identity(row, cluster, desired, current)
                if observed:
                    row.manifest_identities[f"{kind}/{metadata['name']}"] = observed
                    _checkpoint(row, cluster, plugin)
            with _stage(row_id, generation) as (row, cluster, plugin, port):
                desired = _desired_manifest(row, cluster, manifest)
                kind, metadata = desired["kind"], desired["metadata"]
                current = port.call(
                    "get", kind=kind, name=metadata["name"], namespace=metadata.get("namespace")
                )
                observed = _resource_identity(row, cluster, desired, current)
                if observed:
                    metadata.update(observed)
                _authorize(row, cluster, plugin)
                applied = (
                    current
                    if current and kind == "Deployment"
                    else port.call(
                        "apply" if current else "create", desired, namespace=metadata.get("namespace")
                    )
                )
                applied_identity = _uid(applied)
                if observed and observed["uid"] != applied_identity["uid"]:
                    raise AgentInstallError(
                        "IDENTITY_CONFLICT", "Agent resource identity changed during apply"
                    )
                row.manifest_identities[f"{kind}/{metadata['name']}"] = applied_identity
                _checkpoint(row, cluster, plugin)
        with _stage(row_id, generation) as (row, cluster, plugin, _port):
            row.deployment_confirmed = True
            row.status = "awaiting_heartbeat"
            row.error_code = ""
            row.error_message = ""
            _checkpoint(row, cluster, plugin)
            return row.status
    except Exception as exc:
        # Failed stages roll back only their local checkpoint. Earlier confirmed
        # resource UIDs remain durable; uncertain creates can be inspected on retry.
        with transaction.atomic():
            row, _cluster, _plugin = _locked(row_id)
            if row.generation != generation or row.status in {"succeeded", "refused", "awaiting_heartbeat"}:
                return row.status
            if isinstance(exc, PermissionDenied):
                code, message = (
                    "PERMISSION_DENIED",
                    "Original installation authority is no longer available; current credential remains active",
                )
            elif isinstance(exc, AgentInstallError):
                code, message = exc.code, str(exc)
            else:
                code, message = (
                    "PROVIDER_UNCERTAIN",
                    "Installation outcome could not be confirmed; resume the same request",
                )
            if code == "STALE_ATTEMPT":
                return row.status
            row.status = "uncertain" if code == "PROVIDER_UNCERTAIN" else "refused"
            row.error_code, row.error_message = code, message
            row.save()
            return row.status


def _deployment_matches(row, cluster, deployment):
    from core.cluster_management import build_agent_manifests

    if not deployment:
        return False
    expected = _desired_manifest(
        row, cluster, next(m for m in build_agent_manifests(cluster) if m["kind"] == "Deployment")
    )

    # Kubernetes defaults and controller-owned status are allowed; every desired
    # pod-template leaf (including exact Secret refs/image/command) must still match.
    def contains(actual, desired):
        if isinstance(desired, dict):
            return isinstance(actual, dict) and all(
                k in actual and contains(actual[k], v) for k, v in desired.items()
            )
        if isinstance(desired, list):
            return (
                isinstance(actual, list)
                and len(actual) == len(desired)
                and all(contains(a, d) for a, d in zip(actual, desired, strict=True))
            )
        return actual == desired

    return contains(deployment.get("spec"), expected["spec"])


def activate_heartbeat(cluster_guid, key_hash):
    """Only a staged installed credential can activate; current keys remain on the legacy path."""
    candidate = ClusterAgentInstall.objects.filter(
        tenant_cluster__guid=cluster_guid,
        credential_hash=key_hash,
        status="awaiting_heartbeat",
        deployment_confirmed=True,
    ).first()
    if candidate is None:
        return None
    with transaction.atomic():
        row, cluster, plugin = _locked(candidate.pk)
        if row.status != "awaiting_heartbeat" or row.credential_hash != key_hash:
            return None
        try:
            _authorize(row, cluster, plugin)
            port = KubernetesInstallPort(cluster)
            _verify_namespace(row, cluster, port)
            secret = port.call("get", kind="Secret", name=row.secret_name, namespace="astrolift-system")
            if _uid(secret)["uid"] != row.secret_uid or not _secret_matches(row, cluster, secret):
                raise AgentInstallError(
                    "IDENTITY_CONFLICT", "Recorded agent Secret changed; activation refused"
                )
            deployment = port.call(
                "get", kind="Deployment", name=row.deployment_name, namespace="astrolift-system"
            )
            identity = row.manifest_identities.get(f"Deployment/{row.deployment_name}") or {}
            if _uid(deployment)["uid"] != identity.get("uid") or not _deployment_matches(
                row, cluster, deployment
            ):
                raise AgentInstallError(
                    "IDENTITY_CONFLICT", "Recorded agent Deployment changed; activation refused"
                )
            _authorize(row, cluster, plugin)
        except (AgentInstallError, PermissionDenied) as exc:
            if isinstance(exc, AgentInstallError) and exc.code == "PROVIDER_UNCERTAIN":
                row.error_code = "HEARTBEAT_UNCONFIRMED"
                row.error_message = "Candidate heartbeat installation proof is temporarily unavailable; current agent remains active"
            else:
                row.status = "refused"
                row.error_code = "PRECONDITION"
                row.error_message = "Candidate installation source, identity or original authority was withdrawn; current agent remains active"
            row.save()
            return None
        cluster.agent_key_hash = row.credential_hash
        cluster.agent_secret_name = row.secret_name
        cluster.agent_secret_uid = row.secret_uid
        cluster.agent_deployment_uid = identity["uid"]
        cluster.agent_deployment_name = row.deployment_name
        cluster.heartbeat_interval_seconds = row.interval_seconds
        cluster.save(
            update_fields=[
                "agent_key_hash",
                "agent_secret_name",
                "agent_secret_uid",
                "agent_deployment_uid",
                "agent_deployment_name",
                "heartbeat_interval_seconds",
            ]
        )
        row.status = "succeeded"
        row.error_code = ""
        row.error_message = ""
        row.activation_source_digest = _source(cluster, plugin)
        row.heartbeat_confirmed_at = timezone.now()
        row.save()
    retire_previous_deployment(row.pk)
    return cluster


def reconcile_installed_agent(cluster):
    """Keep later legacy deploy/reconcile from adopting a replaced staged object."""
    from _sdk.cluster import ApplyResult

    from core.cluster_management import ClusterManagementError, build_agent_manifests

    with transaction.atomic():
        cluster = TenantCluster.objects.select_for_update().get(pk=cluster.pk)
        if installation_busy(cluster):
            raise ClusterManagementError("A server-owned agent installation is pending")
        row = (
            ClusterAgentInstall.objects.filter(
                tenant_cluster=cluster, status="succeeded", secret_uid=cluster.agent_secret_uid
            )
            .order_by("-id")
            .first()
        )
        if row is None:
            raise ClusterManagementError("Recorded server-owned agent installation is unavailable")
        try:
            port = KubernetesInstallPort(cluster)
            _verify_namespace(row, cluster, port)
            secret = port.call(
                "get", kind="Secret", name=cluster.agent_secret_name, namespace="astrolift-system"
            )
            if _uid(secret)["uid"] != cluster.agent_secret_uid or not _secret_matches(row, cluster, secret):
                raise AgentInstallError(
                    "IDENTITY_CONFLICT", "Recorded agent Secret changed; reconcile refused"
                )
            for manifest in build_agent_manifests(cluster):
                if manifest["kind"] == "Namespace":
                    continue
                desired = _desired_manifest(row, cluster, manifest)
                metadata = desired["metadata"]
                current = port.call(
                    "get", kind=desired["kind"], name=metadata["name"], namespace=metadata.get("namespace")
                )
                observed = _resource_identity(row, cluster, desired, current)
                if observed is None:
                    raise AgentInstallError(
                        "IDENTITY_CONFLICT", "Recorded agent resource disappeared; reconcile refused"
                    )
                metadata.update(observed)
                applied = port.call("apply", desired, namespace=metadata.get("namespace"))
                if _uid(applied)["uid"] != observed["uid"]:
                    raise AgentInstallError("IDENTITY_CONFLICT", "Agent identity changed during reconcile")
        except AgentInstallError as exc:
            raise ClusterManagementError(str(exc)) from None
        return ApplyResult(created=[], updated=[], unchanged=[], errors=[])


def retire_previous_deployment(row_id):
    """Conditional retirement is separate from authenticated activation success."""
    with transaction.atomic():
        row, cluster, plugin = _locked(row_id)
        if row.status != "succeeded" or not row.heartbeat_confirmed_at or row.previous_deployment_retired:
            return
        if not row.previous_deployment_uid:
            return  # No previously observed Deployment; do not adopt one now.
        try:
            _authorize(row, cluster, plugin)
            if (
                cluster.agent_key_hash != row.credential_hash
                or cluster.agent_deployment_name != row.deployment_name
            ):
                raise AgentInstallError("PRECONDITION", "Active agent changed; previous Deployment retained")
            port = KubernetesInstallPort(cluster)
            namespace = port.call("get", kind="Namespace", name="astrolift-system")
            identity = row.manifest_identities.get("Namespace/astrolift-system") or {}
            if not _namespace_owned(cluster, namespace) or _uid(namespace)["uid"] != identity.get("uid"):
                raise AgentInstallError(
                    "IDENTITY_CONFLICT", "Recorded agent Namespace changed; previous Deployment retained"
                )
            previous = port.call(
                "get", kind="Deployment", name=row.previous_deployment_name, namespace="astrolift-system"
            )
            if previous is not None:
                observed = _uid(previous)
                if observed["uid"] != row.previous_deployment_uid:
                    raise AgentInstallError(
                        "IDENTITY_CONFLICT", "Previous agent Deployment was replaced; retirement refused"
                    )
                _authorize(row, cluster, plugin)
                port.call(
                    "delete",
                    {
                        "apiVersion": "v1",
                        "kind": "DeleteOptions",
                        "preconditions": observed,
                        "propagationPolicy": "Foreground",
                    },
                    kind="Deployment",
                    name=row.previous_deployment_name,
                    namespace="astrolift-system",
                )
                previous = port.call(
                    "get", kind="Deployment", name=row.previous_deployment_name, namespace="astrolift-system"
                )
            if previous is not None:
                raise AgentInstallError(
                    "PROVIDER_UNCERTAIN", "Previous agent Deployment retirement could not be confirmed"
                )
            row.previous_deployment_retired = True
            row.error_code = ""
            row.error_message = ""
        except Exception:
            # The new authenticated agent stays active; a failed cleanup must
            # neither undo activation nor adopt/delete an unobserved replacement.
            row.error_code = "RETIREMENT_UNCONFIRMED"
            row.error_message = "Current agent heartbeat is confirmed; previous Deployment retirement is unconfirmed and may need operator review"
        row.save()
