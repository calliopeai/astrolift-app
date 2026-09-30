"""Row filters for operation-aware collections, applied before pagination."""

from __future__ import annotations

import dataclasses
import fnmatch
import functools
import inspect

from django.db.models import Count, Q

from astrolift_identity import abac
from astrolift_identity.operation_context import agent_region_operation, environment_context
from astrolift_identity.permission_resolver import (
    _abac_filter,
    _memoized,
    _org_confined_bindings,
    _policy_scope_permissions,
    _share_grants,
    share_levels,
)
from core.permissions import PermissionDenied, granted_scopes
from core.tenancy import get_current_tenant


def require_app_collection_scope(permission, field="app_slug"):
    """Retain the named-app gate while allowing a filtered org collection."""

    def decorate(fn):
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            from astrolift_agents.visibility import _permitted_apps
            from astrolift_registry.models import RegisteredApp

            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            slug = bound.arguments.get(field)
            if slug:
                tenant = get_current_tenant()
                apps = RegisteredApp.objects.filter(
                    organization_id=tenant.organization_id if tenant else None
                )
                app = apps.filter(slug=slug).first()
                scopes = granted_scopes(tenant, permission)
                if app is None:
                    if not (scopes.org or scopes.org_only):
                        raise PermissionDenied(permission, None, "named app is outside permitted scopes")
                elif not _permitted_apps(apps, permission).filter(pk=app.pk).exists():
                    raise PermissionDenied(permission, None, "named app is outside permitted scopes")
            return fn(*args, **kwargs)

        return wrapped

    return decorate


def _operation_policies(tenant, permission):
    if tenant is None or tenant.organization_id is None:
        return False
    operation_kinds = {kind.kind for kind in abac.CONDITION_KINDS if kind.needs == "operation"}
    return any(
        fnmatch.fnmatchcase(permission.value, policy.action_pattern or "*")
        and (
            any(key in (policy.resource_pattern or {}) for key in ("env", "region"))
            or isinstance(policy.conditions, list)
            and any(
                isinstance(condition, dict) and condition.get("kind") in operation_kinds
                for condition in policy.conditions
            )
        )
        for policy in abac.org_policies(tenant.organization_id, abac.attributes_for(tenant.actor_user_id))
    )


def _evaluator(tenant, permission):
    attrs = abac.attributes_for(tenant.actor_user_id)
    tree_key = ("policy_scope_tree", tenant.organization_id)
    if tree_key not in attrs.cache:
        _policy_scope_permissions(tenant, set())
    points, app_ids = attrs.cache[tree_key]
    chains = {point: chain for point, chain, _contexts in points}
    grants = _org_confined_bindings(tenant)
    shares = _share_grants(tenant, app_ids)

    def allows(kind, ident, context):
        chain = chains.get((kind, ident), [])
        covering = [grant for grant in grants if grant.covers(chain)]
        if kind == "APP":
            covering.extend(
                share.grant
                for share in shares.get(ident, ())
                if share.access_level in share_levels(permission)
            )
        if not any(grant.carries(permission.value) for grant in covering):
            return False
        with abac.operation_attributes(**context.attributes()):
            return bool(_abac_filter(tenant, {permission.value}, chain, covering))

    return allows


def agent_task_facts(tenant):
    """Owned task points and authoritative facts, shared with navigation."""
    from astrolift_agents.models import AgentTask
    from astrolift_identity.operation_context import agent_task_contexts

    attrs = abac.attributes_for(tenant.actor_user_id)
    key = ("agent_task_operation_facts", tenant.organization_id)
    if key not in attrs.cache:
        tasks = list(
            AgentTask.objects.filter(organization_id=tenant.organization_id).select_related(
                "agent_definition__registered_app", "agent_run__app_environment__tenant_cluster"
            )
        )
        contexts = agent_task_contexts(tasks, tenant.organization_id)
        facts = {}
        for task in tasks:
            point = (
                ("PROJECT", task.project_id)
                if task.project_id
                else (
                    ("TEAM", task.team_id)
                    if task.team_id
                    else (
                        ("APP", task.agent_definition.registered_app_id)
                        if task.agent_definition_id and task.agent_definition.kind == "agent"
                        else ("ORG", tenant.organization_id)
                    )
                )
            )
            facts[task.pk] = (point, contexts[task.pk])
        attrs.cache[key] = facts
    return attrs.cache[key]


def agent_scope_contexts(tenant, points):
    """Navigation may use actual agent targets; row reads still check each one."""
    from astrolift_registry.models import Workload

    extra = {}
    default = agent_region_operation({})[0]
    for app_id in Workload.objects.filter(
        registered_app__organization_id=tenant.organization_id, kind=Workload.Kind.AGENT
    ).values_list("registered_app_id", flat=True):
        extra.setdefault(("APP", app_id), set()).add(default)
    for point, context in agent_task_facts(tenant).values():
        extra.setdefault(point, set()).add(context)
    return [
        (point, chain, tuple(dict.fromkeys((*contexts, *extra.get(point, ())))))
        for point, chain, contexts in points
    ]


@_memoized
def visible_agent_task_operations(qs, permission):
    tenant = get_current_tenant()
    if granted_scopes(tenant, permission).org or not _operation_policies(tenant, permission):
        return qs
    allows = _evaluator(tenant, permission)
    return qs.filter(
        pk__in=[pk for pk, (point, context) in agent_task_facts(tenant).items() if allows(*point, context)]
    )


@_memoized
def visible_agent_workload_operations(qs, permission):
    tenant = get_current_tenant()
    if granted_scopes(tenant, permission).org or not _operation_policies(tenant, permission):
        return qs
    context = agent_region_operation({})[0]
    allows = _evaluator(tenant, permission)
    return qs.filter(
        registered_app_id__in=[
            app_id
            for app_id in qs.order_by().values_list("registered_app_id", flat=True).distinct()
            if allows("APP", app_id, context)
        ]
    )


@_memoized
def visible_operation_rows(
    qs,
    permission,
    *,
    app_path="registered_app",
    environment_path="app_environment",
    approvals_field=None,
    app_wide_operations_field=None,
):
    """Filter app-owned operations using distinct persisted fact combinations.

    No policies uses the existing scope subquery. Operation policies load
    environments, scope chains and grants in batches; decisions then use
    those facts without one permission query per returned row.
    """
    from astrolift_agents.visibility import _permitted_apps
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import RegisteredApp

    tenant = get_current_tenant()
    scopes = granted_scopes(tenant, permission)
    if not scopes.org:
        apps = _permitted_apps(
            RegisteredApp.objects.filter(organization_id=tenant.organization_id if tenant else None),
            permission,
        )
        qs = qs.filter(**{f"{app_path}_id__in": apps.values("pk")})
    if scopes.org or not _operation_policies(tenant, permission):
        return qs
    original_qs = qs
    if approvals_field == "approvals_received":
        qs = qs.annotate(
            _verified_approvals=Count(
                "approval_votes__voter_user_id",
                filter=Q(approval_votes__deleted_at__isnull=True),
                distinct=True,
            )
        )
        approvals_field = "_verified_approvals"
    app_field = f"{app_path}_id"
    env_field = "pk" if environment_path == "self" else f"{environment_path}_id"
    fields = [
        app_field,
        env_field,
        *([approvals_field] if approvals_field else []),
        *([app_wide_operations_field] if app_wide_operations_field else []),
    ]
    facts = list(qs.order_by().values_list(*fields).distinct())
    environments = {
        env.pk: env
        for env in AppEnvironment.objects.filter(
            Q(pk__in={fact[1] for fact in facts})
            | Q(registered_app_id__in={fact[0] for fact in facts} if app_wide_operations_field else []),
            registered_app__organization_id=tenant.organization_id,
        ).select_related("tenant_cluster")
    }
    allows = _evaluator(tenant, permission)
    visible = Q(pk__in=[])
    for fact in facts:
        app_id, env_id, *_extra = fact
        approvals = fact[2] if approvals_field else 0
        contexts = [environment_context(environments.get(env_id), approvals=approvals)]
        if app_wide_operations_field and fact[-1] in ("set", "delete", "set_metadata"):
            contexts = [
                environment_context(env, approvals=approvals)
                for env in environments.values()
                if env.registered_app_id == app_id
            ] or [environment_context(None, approvals=approvals)]
        if all(allows("APP", app_id, context) for context in contexts):
            visible |= Q(**dict(zip(fields, fact, strict=True)))
    filtered = qs.filter(visible)
    # Keep the aggregate used for authorization inside the ID subquery:
    # callers can still select the latest deployment with DISTINCT ON.
    return (
        original_qs.filter(pk__in=filtered.values("pk"))
        if approvals_field == "_verified_approvals"
        else filtered
    )


@_memoized
def visible_workflow_operation_rows(qs, permission):
    """A run list reads each exact mirror's environment and approved votes."""
    from astrolift_lifecycle.models import AppEnvironment
    from workflows.models import WorkflowStageExecution

    tenant = get_current_tenant()
    if granted_scopes(tenant, permission).org or not _operation_policies(tenant, permission):
        return qs
    facts = list(
        qs.order_by().values_list(
            "pk", "registered_app_id", "app_environment_id", "workflow_definition__project_id"
        )
    )
    environments = {
        env.pk: env
        for env in AppEnvironment.objects.filter(
            pk__in={fact[2] for fact in facts}, registered_app__organization_id=tenant.organization_id
        ).select_related("tenant_cluster")
    }
    voters = {}
    for run_id, user_id in WorkflowStageExecution.objects.filter(
        workflow_run_id__in=[fact[0] for fact in facts],
        status="completed",
        stage__kind="human_gate",
        output__human_gate__decision="approved",
    ).values_list("workflow_run_id", "output__human_gate__decided_by_user_id"):
        if isinstance(user_id, int) and not isinstance(user_id, bool) and user_id > 0:
            voters.setdefault(run_id, set()).add(user_id)
    allows = _evaluator(tenant, permission)
    agent = agent_region_operation({})[0]
    allowed = []
    for run_id, app_id, env_id, project_id in facts:
        count = len(voters.get(run_id, ()))
        context = (
            environment_context(environments.get(env_id), approvals=count)
            if env_id
            else dataclasses.replace(agent, approvals=count)
        )
        kind, ident = (
            ("APP", app_id)
            if app_id
            else (("PROJECT", project_id) if project_id else ("ORG", tenant.organization_id))
        )
        if allows(kind, ident, context):
            allowed.append(run_id)
    return qs.filter(pk__in=allowed)
