"""Server-produced Endpoint-only app plans; callers must persist accepted operations.

Database admission is separate from fresh native observations. Neither a returned
plan nor an observed catalogue version is a durable acceptance or invoke proof.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, replace

from django.db import connection
from django.db.models import Q

from astrolift_identity import abac
from astrolift_identity.permission_resolver import _decide_from_grants, decide
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.scopes import _app_scope
from astrolift_services.models import (
    ManagedService,
    ManagedServiceAttachment,
    ManagedServiceBinding,
    ManagedServiceVolumeBinding,
)
from astrolift_services.native_identity_authority import current_app_identity_authority
from astrolift_workflows.gcp_identity_inputs import AcceptedPreparationTemplate, EndpointGrant, LogicalSubject
from astrolift_workflows.native_identity_inputs import DeploymentAuthorityContext
from astrolift_workflows.vertex_managed_service import JOURNAL_KEY
from core.app_deploy import namespace_for_environment, workload_identity_role_name
from core.cluster_credentials import credential_for_cluster
from core.permissions import Permission, _check_permission_decision
from core.tenancy import get_current_tenant

MAX_ITEMS = 64
MAX_SOURCE_SECONDS = 120
_VERSION = re.compile(r"[a-zA-Z0-9_-]{1,128}\Z")
_ROLE = re.compile(r"projects/([a-z][a-z0-9-]{4,28}[a-z0-9])/roles/([A-Za-z0-9_.]{1,64})\Z")


class EndpointAppPlanError(ValueError):
    """Fixed reasons only; no provider/configuration bodies escape this seam."""


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _row(row):
    return (str(row.guid), row.version, row.updated_at.isoformat()) if row else None


def _resource(identity, name):
    return "projects/" + identity.project_number + "/" + name.split("/", 2)[2]


@dataclass(frozen=True)
class LifecycleEndpoint:
    service_guid: str
    service_version: int
    endpoint: str
    deployed_model_id: str
    model_artifact: str
    serving_request: tuple[str, int, int, int]


@dataclass(frozen=True)
class EndpointAppSnapshot:
    owners: tuple
    credential_sha256: str
    provider_configuration_sha256: str
    app_configuration_sha256: str
    subjects: tuple[LogicalSubject, ...]
    environments: tuple
    services: tuple
    attachments: tuple
    bindings: tuple
    endpoints: tuple[LifecycleEndpoint, ...]
    prediction_role: str | None


@dataclass(frozen=True)
class ObservedEndpoint:
    resource: str
    # Complete current routing metadata, not a per-deployed-model Predict selector.
    deployments: tuple


@dataclass(frozen=True)
class AcceptedEndpointAppPlan:
    authority: object
    identity: object
    snapshot: EndpointAppSnapshot
    observations: tuple[ObservedEndpoint, ...]
    template: AcceptedPreparationTemplate
    deployment_context: DeploymentAuthorityContext | None = None


def _lifecycle(service, app, cluster, identity):
    if not isinstance(service.lifecycle_policy, dict):
        raise EndpointAppPlanError("VERTEX_LIFECYCLE_INCOMPLETE")
    state = service.lifecycle_policy.get(JOURNAL_KEY)
    if not isinstance(state, dict) or state.get("complete") is not True:
        raise EndpointAppPlanError("VERTEX_LIFECYCLE_INCOMPLETE")
    context = state.get("context", {})
    facts = {
        "service": str(service.guid),
        "organization": str(app.organization.guid),
        "project": str(service.project.guid) if service.project_id else "",
        "cluster": str(cluster.guid),
        "provider": str(cluster.provider_plugin.guid),
        "cloud_project": identity.project_id,
        "region": identity.region,
        "desired_digest": _hash(service.config or {}),
    }
    if not isinstance(context, dict) or any(context.get(k) != v for k, v in facts.items()):
        raise EndpointAppPlanError("VERTEX_LIFECYCLE_SOURCE_CHANGED")
    endpoint, deployed, model = (state.get(k) for k in ("endpoint", "deployed_model_id", "model_artifact"))
    parent = rf"projects/(?:{re.escape(identity.project_id)}|{identity.project_number})/locations/{identity.region}"
    if (
        not isinstance(endpoint, str)
        or not re.fullmatch(parent + r"/endpoints/[A-Za-z0-9_-]{1,128}", endpoint)
        or service.backend_ref != "model_endpoint/" + endpoint
        or not isinstance(deployed, str)
        or not re.fullmatch(r"[0-9]{1,10}", deployed)
        or not isinstance(model, str)
        or not re.fullmatch(parent + r"/models/[A-Za-z0-9_-]{1,128}(?:@[A-Za-z0-9_-]{1,128})?", model)
    ):
        raise EndpointAppPlanError("VERTEX_LIFECYCLE_IDENTITY_UNAVAILABLE")
    serving = state.get("serving_request", {})
    if not isinstance(serving, dict):
        raise EndpointAppPlanError("VERTEX_SERVING_INTENT_UNAVAILABLE")
    machine, minimum, maximum, traffic = (
        serving.get(k)
        for k in ("machine_type", "min_replica_count", "max_replica_count", "traffic_percentage")
    )
    if (
        not isinstance(machine, str)
        or not re.fullmatch(r"[a-zA-Z0-9-]{1,128}", machine)
        or type(minimum) is not int
        or type(maximum) is not int
        or not 1 <= minimum <= maximum <= 1000
        or type(traffic) is not int
        or traffic not in (0, 100)
    ):
        raise EndpointAppPlanError("VERTEX_SERVING_INTENT_UNAVAILABLE")
    return LifecycleEndpoint(
        str(service.guid), service.version, endpoint, deployed, model, (machine, minimum, maximum, traffic)
    )


def endpoint_app_snapshot(authority, identity, *, deployment_context=None):
    """DB-only fresh admission; takes no model locks and makes no SDK/credential discovery calls."""
    from gcp.identity_owned import NativeIdentityContext

    if type(identity) is not NativeIdentityContext:
        raise EndpointAppPlanError("ORIGINAL_IDENTITY_REQUIRED")
    return _endpoint_app_snapshot(authority, identity, deployment_context=deployment_context)


def pre_identity_endpoint_snapshot(authority, scope, *, deployment_context=None):
    """Fresh complete DB union admission before any GSA exists; no guessed UID."""
    from gcp.identity_source import ProjectScope

    if type(scope) is not ProjectScope:
        raise EndpointAppPlanError("VERIFIED_PROJECT_SCOPE_REQUIRED")
    return _endpoint_app_snapshot(authority, scope, deployment_context=deployment_context)


def _endpoint_app_snapshot(authority, identity, *, deployment_context=None):
    with current_app_identity_authority(authority, deployment_context=deployment_context) as selected:
        app, cluster = selected.registered_app, selected.tenant_cluster
        provider = cluster.provider_plugin
        credential = credential_for_cluster(cluster)
        config = cluster.provider_config or {}
        if not isinstance(config, dict):
            raise EndpointAppPlanError("CURRENT_IDENTITY_SOURCE_UNAVAILABLE")
        role = config.get("endpoint_prediction_role")
        if (
            provider.slug != "gcp"
            or identity.organization_id != str(app.organization.guid)
            or identity.app_id != str(app.guid)
            or identity.cluster_id != str(cluster.guid)
            or identity.credential != credential
            or config.get("project_id") not in (identity.project_id, identity.project_number)
            or config.get("vertex_region") != identity.region
        ):
            raise EndpointAppPlanError("CURRENT_IDENTITY_SOURCE_UNAVAILABLE")
        environments = tuple(
            AppEnvironment.objects.filter(registered_app=app, tenant_cluster=cluster)
            .select_related("registered_app__organization", "tenant_cluster")
            .order_by("guid")[: MAX_ITEMS + 1]
        )
        if not environments or len(environments) > MAX_ITEMS:
            raise EndpointAppPlanError("ENVIRONMENT_UNION_BOUND_EXCEEDED")
        scope = _app_scope(pk=app.pk, permission=Permission(authority.permission))
        tenant, permission = get_current_tenant(), Permission(authority.permission)
        decision = decide(tenant, permission, scope)
        aliases = {}
        for env in environments:
            selected_alias = str(env.guid) == authority.environment_guid
            facts = abac.current_attributes()
            with abac.request_attributes(
                replace(
                    facts,
                    environment=env.name,
                    region=env.tenant_cluster.region or None,
                    approvals=facts.approvals if selected_alias else 0,
                    approval_request=facts.approval_request if selected_alias else False,
                )
            ):
                _check_permission_decision(
                    permission,
                    scope,
                    lambda: (
                        decision.as_tuple()
                        if decision.superuser or not decision.rbac_granted
                        else _decide_from_grants(
                            tenant,
                            permission,
                            decision.chain,
                            decision.grants,
                            decision.shares,
                            decision.groups,
                        ).as_tuple()
                    ),
                )
            key = (namespace_for_environment(env), workload_identity_role_name(app))
            aliases.setdefault(key, []).append(str(env.guid))
        subjects = tuple(LogicalSubject(tuple(sorted(ids)), *key) for key, ids in sorted(aliases.items()))
        env_ids = [env.pk for env in environments]
        attachments = tuple(
            ManagedServiceAttachment.objects.filter(app_environment_id__in=env_ids)
            .select_related("managed_service", "app_environment")
            .order_by("guid")[: MAX_ITEMS + 1]
        )
        if len(attachments) > MAX_ITEMS:
            raise EndpointAppPlanError("ATTACHMENT_UNION_BOUND_EXCEEDED")
        # Read the full consumer graph first. A filtered driver collector would
        # silently omit foreign/incompatible attachments and produce a partial union.
        services = tuple(
            ManagedService.all_objects.filter(
                Q(app_environment_id__in=env_ids, deleted_at__isnull=True)
                | Q(pk__in=[row.managed_service_id for row in attachments])
            )
            .select_related("project__organization", "app_environment", "registered_app")
            .order_by("guid")[: MAX_ITEMS + 1]
        )
        if len(services) > MAX_ITEMS:
            raise EndpointAppPlanError("SERVICE_UNION_BOUND_EXCEEDED")
        endpoints = []
        for service in services:
            attached = [row for row in attachments if row.managed_service_id == service.pk]
            direct = service.registered_app_id == app.pk and service.app_environment_id in env_ids
            desired = direct and service.deleted_at is None or any(row.desired_enabled for row in attached)
            project_owned = (
                service.registered_app_id is None
                and service.app_environment_id is None
                and service.organization_id is None
                and app.project_id is not None
                and service.project_id == app.project_id
                and service.project.deleted_at is None
                and service.project.organization_id == app.organization_id
                and service.tenant_cluster_id == cluster.pk
            )
            if (
                not (direct or project_owned)
                or (desired and (service.deleted_at is not None or service.status != "active"))
                or (
                    direct
                    and (
                        service.project_id is not None
                        or service.organization_id is not None
                        or service.tenant_cluster_id is not None
                    )
                )
                or service.kind != "model_endpoint"
                or service.variant not in ("", "vertex_ai")
                or any(row.slice_handle or row.model_subscription for row in attached)
            ):
                raise EndpointAppPlanError("UNSUPPORTED_OR_INCOHERENT_SERVICE_UNION")
            if desired:
                endpoints.append(_lifecycle(service, app, cluster, identity))
        if endpoints and (
            not isinstance(role, str)
            or not _ROLE.fullmatch(role)
            or _ROLE.fullmatch(role)[1] != identity.project_id
        ):
            raise EndpointAppPlanError("PREDICTION_CUSTOM_ROLE_REQUIRED")
        if ManagedServiceVolumeBinding.objects.filter(
            managed_service_id__in=[row.pk for row in services]
        ).exists():
            raise EndpointAppPlanError("UNSUPPORTED_ENDPOINT_VOLUME_BINDING")
        bindings = tuple(
            ManagedServiceBinding.objects.filter(managed_service_id__in=[row.pk for row in services])
            .select_related("managed_service")
            .order_by("guid")[: MAX_ITEMS * 32 + 1]
        )
        if len(bindings) > MAX_ITEMS * 32:
            raise EndpointAppPlanError("BINDING_UNION_BOUND_EXCEEDED")
        return EndpointAppSnapshot(
            (
                _row(app.organization),
                _row(app.team),
                _row(app.project),
                _row(app),
                _row(cluster),
                _row(provider),
            ),
            _hash(asdict(credential)),
            _hash((config, cluster.auth_config)),
            _hash((app.manifest_normalized, app.manifest_hash)),
            tuple(subjects),
            tuple((_row(env), env.name, env.k8s_namespace, _hash(env.deploy_config)) for env in environments),
            tuple(
                (
                    _row(row),
                    str(row.registered_app.guid) if row.registered_app_id else None,
                    str(row.app_environment.guid) if row.app_environment_id else None,
                    str(row.project.guid) if row.project_id else None,
                    row.status,
                    _hash(row.config),
                    _hash(row.applied_config),
                    _hash(row.lifecycle_policy),
                    row.backend_ref,
                    row.deleted_at.isoformat() if row.deleted_at else None,
                    row.operation_kind,
                    row.operation_workflow_id,
                    row.operation_run_id,
                    tuple(row.bind_workloads or []),
                )
                for row in services
            ),
            tuple(
                (
                    _row(row),
                    str(row.managed_service.guid),
                    str(row.app_environment.guid),
                    row.desired_enabled,
                    row.desired_revision,
                    row.applied_revision,
                    row.subscription_status,
                    row.binding_alias,
                    _hash(row.credential_ref),
                    tuple(row.workload_names or []),
                )
                for row in attachments
            ),
            tuple(
                (
                    _row(row),
                    str(row.managed_service.guid),
                    row.env_key,
                    row.is_secret,
                    _hash(row.env_value_ref),
                )
                for row in bindings
            ),
            tuple(endpoints),
            role if isinstance(role, str) else None,
        )


class NativeEndpointObserver:
    """Real fixed-host registered-source clients; no binding, allocation or IAM setter."""

    def __init__(self, identity, *, checkpoint):
        from gcp.identity_owned import NativeGCPIdentity
        from gcp.vertex_catalogue import VertexCatalogue, VertexCatalogueConfig

        checkpoint()
        self.checkpoint = checkpoint
        self.identity = identity
        self.roles = NativeGCPIdentity(identity)
        try:
            self.catalogue = VertexCatalogue(
                VertexCatalogueConfig(identity.project_id, identity.region, identity.credential),
                checkpoint=checkpoint,
            )
        except BaseException:
            self.roles.close()
            raise

    def close(self):
        self.roles.close()
        self.catalogue.close()

    def observe(self, snapshot):
        from gcp.vertex_catalogue import CatalogueState

        if not snapshot.endpoints:
            return ()
        self.roles.verify_prediction_roles((snapshot.prediction_role,), checkpoint=self.checkpoint)
        observations = []
        try:
            for endpoint in sorted({row.endpoint for row in snapshot.endpoints}):
                owners = [row.service_guid for row in snapshot.endpoints if row.endpoint == endpoint]
                if len(owners) != 1:
                    raise EndpointAppPlanError("AMBIGUOUS_ENDPOINT_LIFECYCLE_OWNER")
                self.roles.verify_owned_endpoint(endpoint, owners[0], checkpoint=self.checkpoint)
                result = self.catalogue.endpoint_detail(endpoint)
                if (
                    result.state != CatalogueState.METADATA
                    or result.truncated
                    or len(result.items) != 1
                    or result.identity is None
                    or result.identity.project_id != self.identity.project_id
                    or result.identity.project_number != self.identity.project_number
                    or result.identity.region != self.identity.region
                ):
                    raise EndpointAppPlanError("CURRENT_ENDPOINT_METADATA_UNAVAILABLE")
                source = result.items[0]
                if source.kind != "endpoint" or _resource(self.identity, source.name) != _resource(
                    self.identity, endpoint
                ):
                    raise EndpointAppPlanError("CURRENT_ENDPOINT_IDENTITY_CHANGED")
                observations.append(
                    ObservedEndpoint(
                        endpoint,
                        tuple(
                            sorted(
                                (
                                    row.id,
                                    row.model,
                                    row.model_version_id,
                                    row.machine_type,
                                    row.min_replicas,
                                    row.max_replicas,
                                    row.available_replicas,
                                    row.traffic_percent,
                                )
                                for row in source.deployments
                            )
                        ),
                    )
                )
                self.roles.verify_owned_endpoint(endpoint, owners[0], checkpoint=self.checkpoint)
            self.roles.verify_prediction_roles((snapshot.prediction_role,), checkpoint=self.checkpoint)
            return tuple(observations)
        except EndpointAppPlanError:
            raise
        except Exception:
            raise EndpointAppPlanError("CURRENT_ENDPOINT_SOURCE_UNCONFIRMED") from None


def _validate_observations(snapshot, observations):
    if type(observations) is not tuple or len(observations) != len(
        {row.endpoint for row in snapshot.endpoints}
    ):
        raise EndpointAppPlanError("INCOMPLETE_ENDPOINT_OBSERVATIONS")
    if len({row.resource for row in observations}) != len(observations):
        raise EndpointAppPlanError("INCOMPLETE_ENDPOINT_OBSERVATIONS")
    for endpoint in snapshot.endpoints:
        observed = next((row for row in observations if row.resource == endpoint.endpoint), None)
        # Owned provisioning currently accepts exactly one deployed model and
        # its complete traffic tuple. Do not grant an unreviewed sibling route.
        if observed is None or len(observed.deployments) != 1:
            raise EndpointAppPlanError("ENDPOINT_ROUTING_UNION_CHANGED")
        deployed, model, version, machine, minimum, maximum, available, traffic = observed.deployments[0]
        base, _, pinned = endpoint.model_artifact.partition("@")
        native_base, _, native_pin = model.partition("@")
        if (
            deployed != endpoint.deployed_model_id
            or native_base != base
            or not isinstance(version, str)
            or not _VERSION.fullmatch(version)
            or (pinned and pinned != version)
            or (native_pin and native_pin != version)
            or (machine, minimum, maximum, traffic) != endpoint.serving_request
            or type(available) is not int
            or available < 0
        ):
            raise EndpointAppPlanError("CURRENT_ENDPOINT_VERSION_OR_SERVING_CHANGED")


def _template(authority, identity, snapshot, observations, *, deployment_context=None):
    _validate_observations(snapshot, observations)
    return AcceptedPreparationTemplate(
        tuple(
            EndpointGrant(snapshot.prediction_role, endpoint)
            for endpoint in sorted({_resource(identity, row.endpoint) for row in snapshot.endpoints})
        ),
        snapshot.subjects,
        _hash(
            {
                "schema": "astrolift.gcp-app-endpoint-source.v1",
                **({"deployment": asdict(deployment_context)} if deployment_context is not None else {}),
                "authority": asdict(authority),
                "identity": asdict(identity),
                "database": asdict(snapshot),
                "native_metadata": tuple(
                    {
                        "resource": row.resource,
                        "deployments": tuple(
                            (*deployment[:6], deployment[7]) for deployment in row.deployments
                        ),
                    }
                    for row in observations
                ),
            }
        ),
    )


def endpoint_app_checkpoint(plan):
    """DB-only callable usable at journal checkpoints; no new model locks or cloud reads."""

    def current():
        snapshot = endpoint_app_snapshot(
            plan.authority, plan.identity, deployment_context=plan.deployment_context
        )
        if (
            snapshot != plan.snapshot
            or _template(
                plan.authority,
                plan.identity,
                snapshot,
                plan.observations,
                deployment_context=plan.deployment_context,
            )
            != plan.template
        ):
            raise EndpointAppPlanError("ACCEPTED_APP_SOURCE_CHANGED")

    return current


def refresh_endpoint_app_sources(plan, *, observer_factory=NativeEndpointObserver):
    """Separate bounded native stage; never run from a database/journal transaction."""
    if connection.in_atomic_block or not connection.get_autocommit():
        raise EndpointAppPlanError("NATIVE_OBSERVATION_REQUIRES_COMMITTED_DATABASE")
    current = endpoint_app_checkpoint(plan)
    deadline = time.monotonic() + MAX_SOURCE_SECONDS

    def checkpoint():
        if time.monotonic() >= deadline:
            raise EndpointAppPlanError("NATIVE_SOURCE_DEADLINE_EXCEEDED")
        current()

    checkpoint()
    if not plan.snapshot.endpoints:
        if (
            plan.observations
            or _template(
                plan.authority, plan.identity, plan.snapshot, (), deployment_context=plan.deployment_context
            )
            != plan.template
        ):
            raise EndpointAppPlanError("ACCEPTED_NATIVE_SOURCE_CHANGED")
        return
    observer = observer_factory(plan.identity, checkpoint=checkpoint)
    try:
        observations = observer.observe(plan.snapshot)
        checkpoint()
        if (
            _template(
                plan.authority,
                plan.identity,
                plan.snapshot,
                observations,
                deployment_context=plan.deployment_context,
            )
            != plan.template
        ):
            raise EndpointAppPlanError("ACCEPTED_NATIVE_SOURCE_CHANGED")
    finally:
        observer.close()


def produce_endpoint_app_plan(
    authority, identity, *, observer_factory=NativeEndpointObserver, deployment_context=None
):
    if connection.in_atomic_block or not connection.get_autocommit():
        raise EndpointAppPlanError("NATIVE_OBSERVATION_REQUIRES_COMMITTED_DATABASE")
    snapshot = endpoint_app_snapshot(authority, identity, deployment_context=deployment_context)
    deadline = time.monotonic() + MAX_SOURCE_SECONDS

    def checkpoint():
        if time.monotonic() >= deadline:
            raise EndpointAppPlanError("NATIVE_SOURCE_DEADLINE_EXCEEDED")
        if endpoint_app_snapshot(authority, identity, deployment_context=deployment_context) != snapshot:
            raise EndpointAppPlanError("ACCEPTED_APP_SOURCE_CHANGED")

    checkpoint()
    if not snapshot.endpoints:
        return AcceptedEndpointAppPlan(
            authority,
            identity,
            snapshot,
            (),
            _template(authority, identity, snapshot, (), deployment_context=deployment_context),
            deployment_context,
        )
    observer = observer_factory(identity, checkpoint=checkpoint)
    try:
        observations = observer.observe(snapshot)
        checkpoint()
        template = _template(
            authority, identity, snapshot, observations, deployment_context=deployment_context
        )
        return AcceptedEndpointAppPlan(
            authority, identity, snapshot, observations, template, deployment_context
        )
    finally:
        observer.close()
