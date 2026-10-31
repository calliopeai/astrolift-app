"""Reprovision an app's environments against the release each is running (#2573).

The platform half of an environment (its zone binding and URL, hostname
claims, Ingress and certificate request, DNS, edge annotations, network
policy and bindings) could only be re-rendered by a deploy, which rebuilds,
or by force redeploy, which deletes the workloads. ``reprovision`` is the
cheap path: per environment it binds a zone that registration could not,
rewrites the URL, reconciles the claims, and, when a release is running,
records a ``trigger_kind=reprovision`` deployment pinned to that release and
starts ``ReprovisionAppWorkflow`` for it.

It returns within a bounded wait: an environment whose run finished in time
reports the objects its apply created and updated, and one still running
reports ``provisioning`` with its deployment, which then settles on its own.
Calling it again while a reprovision runs joins that run.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from django.db import transaction
from django.utils import timezone

from astrolift_lifecycle.reachability import (
    PROVISIONING,
    ClusterReader,
    environment_url,
    environment_verdicts,
    usable_domain,
)
from astrolift_workflows.client import (
    WorkflowAlreadyRunning,
    WorkflowHandle,
    start_workflow_exclusive,
    wait_for_workflows,
)

log = logging.getLogger(__name__)

#: How long the request waits for the runs it started, under the 30 s a CLI
#: allows a request, with room for the reads around it.
WAIT_SECONDS = 12.0

_IN_FLIGHT = ("pending", "deploying", "redeploying")


class ReprovisionRefused(Exception):
    """Nothing was written: the request names something missing or not allowed now."""

    def __init__(self, code: str, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


@dataclasses.dataclass
class EnvironmentResult:
    environment_name: str
    deployment_guid: str | None
    status: str
    created: list[str]
    updated: list[str]
    unchanged: bool
    error: str = ""


@dataclasses.dataclass
class _Plan:
    env: Any
    running: Any = None
    joined: Any = None
    deployment: Any = None
    handle: WorkflowHandle | None = None
    created: list[str] = dataclasses.field(default_factory=list)
    updated: list[str] = dataclasses.field(default_factory=list)
    error: str = ""


def _environments(app, environment_name: str | None) -> list[Any]:
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_lifecycle.visibility import live_lifecycle_rows

    qs = (
        live_lifecycle_rows(AppEnvironment.objects.all())
        .filter(registered_app=app)
        .select_related("registered_app__organization", "tenant_cluster__provider_plugin", "managed_domain")
        .order_by("name")
    )
    if environment_name:
        qs = qs.filter(name=environment_name)
        if not qs.exists():
            raise ReprovisionRefused(
                "NOT_FOUND",
                f"environment {environment_name!r} not found on app {app.slug!r}",
                field="environmentName",
            )
    return list(qs)


def _plan(app, env) -> _Plan:
    from astrolift_lifecycle.deployment_identity_origin import native_origin_required
    from astrolift_lifecycle.models import Deployment
    from astrolift_lifecycle.schema.mutations.helpers import _required_approvals_for

    if env.deploys_paused:
        raise ReprovisionRefused(
            "PRECONDITION", f"environment {env.name!r} has deploys paused", field="environmentName"
        )
    if native_origin_required(app, env):
        raise ReprovisionRefused(
            "PRECONDITION",
            f"environment {env.name!r} runs a native GKE Endpoint; a deploy reprovisions it",
            field="environmentName",
        )
    deployments = Deployment.objects.filter(app_environment=env, deleted_at__isnull=True)
    in_flight = deployments.filter(status__in=_IN_FLIGHT).order_by("-created_at", "-pk").first()
    if in_flight is not None:
        if in_flight.trigger_kind == Deployment.TriggerKind.REPROVISION.value:
            return _Plan(env=env, joined=in_flight)
        raise ReprovisionRefused(
            "PRECONDITION",
            f"a deployment is in flight for environment {env.name!r}; it renders the environment when it finishes",
            field="environmentName",
        )
    running = (
        deployments.filter(status=Deployment.Status.RUNNING.value).order_by("-created_at", "-pk").first()
    )
    if running is not None and _required_approvals_for(app, env) > 0:
        # Reprovision renders the current manifest, so on an environment that
        # gates deploys on approval it must not carry a manifest change the
        # approvers never saw.
        live = str((running.config_snapshot or {}).get("manifest_hash", "")).strip()
        if live and live != (app.manifest_hash or "").strip():
            raise ReprovisionRefused(
                "PRECONDITION",
                f"environment {env.name!r} requires deploy approval and its manifest changed since the running "
                "deployment; deploy the change instead",
                field="environmentName",
            )
    return _Plan(env=env, running=running)


def _bind(plan: _Plan) -> None:
    """Bind a zone registration could not, and write the URL that follows."""
    from astrolift_clusters.models import resolve_managed_domain
    from astrolift_lifecycle.models import PreviewEnvironment
    from astrolift_registry.hostname_claims import hostname_label_refusal

    env = plan.env
    app = env.registered_app
    fields: list[str] = []
    domain = usable_domain(env.managed_domain if env.managed_domain_id else None)
    if domain is None:
        preview = (
            env.previewed_environment_id is not None
            or PreviewEnvironment.all_objects.filter(app_environment=env).exists()
        )
        domain = resolve_managed_domain(app.organization, for_preview=preview)
        if domain is not None:
            refusal = hostname_label_refusal(
                app.subdomain or app.slug, organization=app.organization, managed_domain=domain
            )
            if refusal:
                raise ReprovisionRefused("PRECONDITION", refusal, field="appSlug")
            env.managed_domain = domain
            fields.append("managed_domain")
    if domain is not None:
        url = environment_url(env, domain)
        if url and url != env.url:
            env.url = url
            fields.append("url")
    if fields:
        env.save(update_fields=[*fields, "updated_at", "version"])
        plan.updated.append(f"AppEnvironment/{env.name}")


def _claims(app, plans: list[_Plan]) -> None:
    from astrolift_registry.hostname_claims import sync_workload_hostname_claims
    from astrolift_registry.models import HostnameClaim

    def held() -> dict[int, set[str]]:
        out: dict[int, set[str]] = {}
        for env_id, hostname in HostnameClaim.objects.filter(
            registered_app=app, deleted_at__isnull=True
        ).values_list("environment_id", "hostname"):
            out.setdefault(env_id, set()).add(hostname)
        return out

    before = held()
    for conflict in sync_workload_hostname_claims(app):
        log.info("reprovision: app %s hostname claim not taken: %s", app.slug, conflict)
    after = held()
    for plan in plans:
        new = after.get(plan.env.pk, set()) - before.get(plan.env.pk, set())
        plan.created.extend(f"HostnameClaim/{host}" for host in sorted(new))


def _record(plan: _Plan, actor) -> bool:
    """The reprovision row, pinned to the running release, under the environment's lock."""
    from astrolift_lifecycle.models import AppEnvironment, Deployment

    running = plan.running
    with transaction.atomic():
        AppEnvironment.objects.select_for_update().filter(pk=plan.env.pk).first()
        if Deployment.objects.filter(
            app_environment=plan.env, status__in=_IN_FLIGHT, deleted_at__isnull=True
        ).exists():
            plan.error = "a deployment started while this was being prepared; it renders the environment"
            return False
        plan.deployment = Deployment.objects.create(
            registered_app_id=running.registered_app_id,
            app_environment_id=plan.env.pk,
            workload_id=running.workload_id,
            triggered_by_user_id=actor.user_id,
            trigger_kind=Deployment.TriggerKind.REPROVISION.value,
            strategy=running.strategy or "rolling",
            status=Deployment.Status.PENDING.value,
            image_tag=running.image_tag,
            image_digest=running.image_digest,
            config_snapshot=running.config_snapshot,
            # The running release's literal [env] values, so the apply renders
            # the Secret digest the pods already run and rolls nothing for it.
            secret_snapshot=running.secret_snapshot,
            approvals_required=0,
            approvals_received=0,
            promoted_from=running,
            commit_sha=running.commit_sha,
            branch=running.branch,
        )
        AppEnvironment.all_objects.filter(pk=plan.env.pk).update(
            reachability_status=PROVISIONING,
            reachability_detail="Reprovision started.",
            reachability_checked_at=timezone.now(),
        )
    return True


def _fail(plan: _Plan, reason: str) -> None:
    plan.error = reason
    deployment = plan.deployment
    if not deployment.aborted_reason:
        deployment.aborted_reason = reason[:2000]
        deployment.save(update_fields=["aborted_reason", "updated_at", "version"])
    deployment.transition_to(deployment.Status.FAILED)


def _start(app, plan: _Plan, actor, organization_id: int | None) -> None:
    from astrolift_identity.api_tokens import get_current_api_token
    from astrolift_lifecycle.schema.mutations.helpers import _deploy_workflow_id, _record_workflow_run
    from astrolift_workflows.inputs import ReprovisionInput
    from core.events import Event
    from core.run_trigger import deployment_run_trigger

    deployment = plan.deployment
    try:
        handle = start_workflow_exclusive(
            "ReprovisionAppWorkflow",
            [
                ReprovisionInput(
                    deployment_id=deployment.pk,
                    registered_app_id=app.pk,
                    app_environment_id=plan.env.pk,
                    actor=actor,
                )
            ],
            # The environment's deploy id: one run at a time per environment,
            # and a newer deploy signals and replaces this one as it would a deploy.
            workflow_id=_deploy_workflow_id(str(app.guid), str(plan.env.guid)),
        )
    except WorkflowAlreadyRunning:
        _fail(plan, "a deployment started first")
        return
    except Exception as exc:  # noqa: BLE001 - recorded on the row and returned, never raised
        log.warning("reprovision: could not start for %s/%s", app.slug, plan.env.name, exc_info=True)
        _fail(plan, f"could not start the reprovision: {exc}")
        return
    if not handle.enqueued:
        _fail(plan, "the workflow engine is off on this install")
        return
    plan.handle = handle
    run = _record_workflow_run(
        kind="ReprovisionAppWorkflow",
        workflow_id=handle.workflow_id,
        run_id=handle.run_id,
        organization_id=organization_id,
        registered_app_id=app.pk,
        app_environment_id=plan.env.pk,
        actor=actor,
        trigger_kind=deployment_run_trigger(
            deployment.trigger_kind, via_token=get_current_api_token() is not None
        ),
    )
    deployment.workflow_run = run
    deployment.save(update_fields=["workflow_run", "updated_at", "version"])
    Event.emit(
        "deploy.started",
        payload={
            "deployment_guid": str(deployment.guid),
            "workflow_kind": "ReprovisionAppWorkflow",
            "workflow_id": handle.workflow_id,
            "trigger_kind": deployment.trigger_kind,
        },
        resource_kind="deployment",
        resource_id=str(deployment.guid),
        organization_id=organization_id,
        registered_app_id=app.pk,
    )


def _collect(plans: list[_Plan]) -> set[int]:
    """Wait for the runs, fold in what each applied; the envs still running."""
    started = [p for p in plans if p.handle is not None]
    still_running: set[int] = set()
    for plan, outcome in zip(
        started, wait_for_workflows([p.handle for p in started], timeout_seconds=WAIT_SECONDS), strict=True
    ):
        if outcome.state == "running":
            still_running.add(plan.env.pk)
            continue
        if outcome.state == "failed":
            plan.error = outcome.error or "the reprovision failed"
            continue
        result = outcome.result if isinstance(outcome.result, dict) else {}
        applied = result.get("data") if isinstance(result.get("data"), dict) else {}
        plan.created.extend(applied.get("created") or [])
        plan.updated.extend(applied.get("updated") or [])
        if not result.get("ok", False):
            plan.error = str(result.get("message") or "the reprovision failed")
    return still_running


def reprovision(
    app, *, environment_name: str | None, actor, organization_id: int | None
) -> list[EnvironmentResult]:
    """Reprovision ``app``'s environments, or the one named; raises ``ReprovisionRefused``
    before writing anything when a precondition fails."""
    from astrolift_workflows.client import _temporal_enabled

    plans = [_plan(app, env) for env in _environments(app, environment_name)]
    if not plans:
        raise ReprovisionRefused("PRECONDITION", f"app {app.slug!r} has no environment to reprovision")
    if any(p.running is not None for p in plans) and not _temporal_enabled():
        raise ReprovisionRefused(
            "PRECONDITION", "the workflow engine is off on this install, and reprovision runs on it"
        )
    with transaction.atomic():
        for plan in plans:
            _bind(plan)
        _claims(app, plans)
    for plan in plans:
        if plan.running is not None and _record(plan, actor):
            _start(app, plan, actor, organization_id)
    still_running = _collect(plans)

    verdicts = environment_verdicts([p.env for p in plans], reader=ClusterReader())
    results = []
    for plan in plans:
        deployment = plan.joined or plan.deployment
        busy = plan.joined is not None or plan.env.pk in still_running
        results.append(
            EnvironmentResult(
                environment_name=plan.env.name,
                deployment_guid=str(deployment.guid) if deployment is not None else None,
                status=verdicts[plan.env.pk].status,
                created=plan.created,
                updated=plan.updated,
                unchanged=not busy and not plan.created and not plan.updated,
                error=plan.error,
            )
        )
    return results
