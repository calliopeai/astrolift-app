"""Actual operation owners and credential ceilings, independent of selection."""

from __future__ import annotations

import uuid

from django.db.models import Q

from astrolift_registry.scopes import app_scope_by_guid, app_scope_by_slug, live_app_owners
from astrolift_services.scopes import _credential_scope, managed_service_scope_by_guid
from core.permissions import PermissionScope, ScopeKind
from core.scope_args import read_arg, read_guid
from core.tenancy import get_current_tenant


def org_id():
    tenant = get_current_tenant()
    return tenant.organization_id if tenant else None


def org_scope(permission):
    def resolve(_args):
        return _credential_scope(PermissionScope(kind=ScopeKind.ORG, id=org_id() or 0), (permission,))

    return resolve


def apps():
    from astrolift_registry.models import RegisteredApp

    return live_app_owners(
        RegisteredApp.objects.filter(
            organization_id=org_id(), organization__deleted_at__isnull=True, deleted_at__isnull=True
        )
    )


def _identifier(value, slug_field="slug"):
    try:
        return Q(guid=uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return Q(**{slug_field: str(value)})


def target_scope(target, ident, permission):
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import Workload

    if not ident or target not in ("app", "env", "workload"):
        return org_scope(permission)({})
    if target == "app":
        rows = apps().filter(_identifier(ident))
        owners = list(rows.values_list("guid", flat=True)[:2])
    else:
        model = AppEnvironment if target == "env" else Workload
        rows = model.objects.filter(
            _identifier(ident, "name" if target == "env" else "slug"),
            registered_app__in=apps(),
            deleted_at__isnull=True,
        )
        owners = list(rows.values_list("registered_app__guid", flat=True)[:2])
    if len(owners) != 1:
        return org_scope(permission)({})
    return app_scope_by_guid(permission=permission)({"app_id": owners[0]})


def target_exists(target, ident):
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_registry.models import Workload

    if target == "global":
        return not ident
    if target == "app":
        rows = apps().filter(_identifier(ident))
    elif target in ("env", "workload"):
        model = AppEnvironment if target == "env" else Workload
        rows = model.objects.filter(
            _identifier(ident, "name" if target == "env" else "slug"),
            registered_app__in=apps(),
            deleted_at__isnull=True,
        )
    else:
        return False
    return bool(ident) and rows.count() == 1


def alert_operation(field="input.id", *, event=False, creation=False):
    def load(args):
        from astrolift_identity.operation_context import (
            UNKNOWN,
            environment_operation,
            managed_service_operation,
            named_environment,
            workload_operation,
        )
        from astrolift_operations.models import AlertEvent, AlertRule

        if creation:
            target, ident = read_arg(args, "input.target"), read_arg(args, "input.target_id")
            service = read_guid(args, "input.managed_service_id")
        else:
            guid = read_guid(args, field)
            if not guid:
                return UNKNOWN
            if event:
                row = (
                    AlertEvent.objects.filter(guid=guid, organization_id=org_id(), deleted_at__isnull=True)
                    .select_related("rule__managed_service")
                    .first()
                )
                rule = row.rule if row else None
            else:
                rule = (
                    AlertRule.objects.filter(guid=guid, organization_id=org_id(), deleted_at__isnull=True)
                    .select_related("managed_service")
                    .first()
                )
            if rule is None:
                return UNKNOWN
            target, ident = rule.target, rule.target_id
            service = rule.managed_service.guid if rule.managed_service_id else None
        if service:
            return managed_service_operation("service_id")({"service_id": service})
        if target == "env":
            from astrolift_lifecycle.models import AppEnvironment

            rows = list(
                AppEnvironment.objects.filter(
                    _identifier(ident, "name" if target == "env" else "slug"),
                    registered_app__in=apps(),
                    deleted_at__isnull=True,
                ).values_list("guid", flat=True)[:2]
            )
            return (
                environment_operation("environment_id")({"environment_id": rows[0]})
                if len(rows) == 1
                else UNKNOWN
            )
        if target == "workload":
            from astrolift_registry.models import Workload

            rows = list(
                Workload.objects.filter(
                    _identifier(ident, "name" if target == "env" else "slug"),
                    registered_app__in=apps(),
                    deleted_at__isnull=True,
                ).values_list("guid", flat=True)[:2]
            )
            return workload_operation("workload_id")({"workload_id": rows[0]}) if len(rows) == 1 else UNKNOWN
        if target == "app":
            row = apps().filter(_identifier(ident)).first()
            return (
                named_environment("app_slug", "_absent", all_if_absent=True)({"app_slug": row.slug})
                if row
                else UNKNOWN
            )
        return UNKNOWN

    return load


def rule_owner(rule, permission):
    if rule is None:
        return org_scope(permission)({})
    if rule.managed_service_id:
        return managed_service_scope_by_guid(permissions=(permission,))(
            {"managed_service_id": rule.managed_service.guid}
        )
    return target_scope(rule.target, rule.target_id, permission)


def alert_scope(permission, field="input.id", *, event=False):
    def resolve(args):
        from astrolift_operations.models import AlertEvent, AlertRule

        guid = read_guid(args, field)
        if not guid:
            return org_scope(permission)({})
        if event:
            row = (
                AlertEvent.objects.filter(
                    guid=guid,
                    organization_id=org_id(),
                    rule__organization_id=org_id(),
                    deleted_at__isnull=True,
                    rule__deleted_at__isnull=True,
                )
                .select_related("rule__managed_service")
                .first()
            )
            rule = row.rule if row else None
        else:
            rule = (
                AlertRule.objects.filter(guid=guid, organization_id=org_id(), deleted_at__isnull=True)
                .select_related("managed_service")
                .first()
            )
        return rule_owner(rule, permission)

    return resolve


def alert_creation_scope(permission):
    def resolve(args):
        service = read_guid(args, "input.managed_service_id")
        if service:
            return managed_service_scope_by_guid(permissions=(permission,))({"managed_service_id": service})
        return target_scope(read_arg(args, "input.target"), read_arg(args, "input.target_id"), permission)

    return resolve


def webhook_owner(row, permission):
    if row is None:
        return org_scope(permission)({})
    if row.team_id and (row.team.organization_id != org_id() or row.team.deleted_at is not None):
        return org_scope(permission)({})
    if row.registered_app_id:
        return app_scope_by_guid(permission=permission)({"app_id": row.registered_app.guid})
    if row.team_id and row.team.organization_id == org_id() and row.team.deleted_at is None:
        return _credential_scope(PermissionScope(kind=ScopeKind.TEAM, id=row.team_id), (permission,))
    return org_scope(permission)({})


def webhook_scope(permission, field="input.id"):
    def resolve(args):
        from astrolift_operations.models import WebhookSubscription

        guid = read_guid(args, field)
        row = (
            WebhookSubscription.objects.filter(guid=guid, organization_id=org_id(), deleted_at__isnull=True)
            .select_related("registered_app", "team")
            .first()
            if guid
            else None
        )
        return webhook_owner(row, permission)

    return resolve


def webhook_creation_scope(permission):
    def resolve(args):
        from astrolift_identity.models import Team

        slug = read_arg(args, "input.app_slug")
        if slug:
            return app_scope_by_slug("input.app_slug", permission=permission)(args)
        slug = read_arg(args, "input.team_slug")
        team = (
            Team.objects.filter(organization_id=org_id(), slug=slug, deleted_at__isnull=True).first()
            if slug
            else None
        )
        return (
            _credential_scope(PermissionScope(kind=ScopeKind.TEAM, id=team.pk), (permission,))
            if team
            else org_scope(permission)({})
        )

    return resolve


def subscription_scope(permission):
    def resolve(args):
        from astrolift_operations.models import UserAlertSubscription

        tenant = get_current_tenant()
        guid = read_guid(args, "input.id")
        row = (
            UserAlertSubscription.objects.filter(
                guid=guid,
                user_id=tenant.actor_user_id if tenant else None,
                registered_app__organization_id=org_id(),
                deleted_at__isnull=True,
            )
            .select_related("registered_app")
            .first()
            if guid
            else None
        )
        return (
            app_scope_by_guid(permission=permission)({"app_id": row.registered_app.guid})
            if row
            else org_scope(permission)({})
        )

    return resolve


def provider_read_operation(permission, *, metrics=False):
    def load(args):
        from astrolift_identity.operation_context import UNKNOWN, OperationContext, environment_context
        from astrolift_lifecycle.models import AppEnvironment
        from astrolift_services.scopes import assert_provider_cluster

        slug = read_arg(args, "app_slug" if metrics else "input.app_slug")
        app = apps().filter(slug=slug).select_related("default_tenant_cluster").first() if slug else None
        if app is None:
            return UNKNOWN
        if not metrics:
            from graphql import GraphQLError

            from astrolift_lifecycle.preview_log_access import resolve_preview_log_target

            try:
                reviewed = resolve_preview_log_target(
                    slug,
                    permission=permission,
                    environment_name=read_arg(args, "input.environment_name"),
                    **{
                        key: read_arg(args, f"input.{key}")
                        for key in (
                            "preview_id",
                            "expected_environment_id",
                            "if_match_preview_version",
                            "if_match_environment_version",
                        )
                    },
                )
            except GraphQLError:
                return UNKNOWN  # The resolver supplies the structured precondition refusal.
            if reviewed is not None:
                return (environment_context(reviewed[0].app_environment),)
        name = read_arg(args, "input.environment_name") if not metrics else None
        if metrics or name:
            envs = AppEnvironment.objects.filter(registered_app=app, deleted_at__isnull=True).select_related(
                "tenant_cluster"
            )
            env = envs.order_by("name").first() if metrics else envs.filter(name=name).first()
            if env is None:
                return UNKNOWN
            assert_provider_cluster(env.tenant_cluster, permission=permission)
            return (environment_context(env),)
        cluster = app.default_tenant_cluster
        if cluster is None:
            return UNKNOWN
        assert_provider_cluster(cluster, permission=permission)
        return (OperationContext(region=cluster.region or None, approvals=0),)

    return load
