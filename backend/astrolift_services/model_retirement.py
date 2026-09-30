"""Refuse retiring consumer ancestry until independent model revocation is confirmed."""

from contextlib import contextmanager
from functools import wraps
from inspect import signature

from django.db import transaction
from django.db.models import F

REVOCATION_REQUIRED = (
    "Revoke every shared model subscription and wait for confirmed revocation before retiring this resource."
)
MODEL_CLEANUP_REQUIRED = (
    "Deprovision every organization-owned shared model deployment before retiring this organization."
)


class ModelSubscriptionRetirementBlocked(ValueError):
    pass


@contextmanager
def locked_retirement_apps(apps):
    from astrolift_lifecycle.models import AppEnvironment
    from astrolift_services.models import ManagedServiceAttachment

    with transaction.atomic():
        locked = list(apps.select_for_update().order_by("pk"))
        ids = [app.pk for app in locked]
        list(AppEnvironment.all_objects.select_for_update().filter(registered_app_id__in=ids).order_by("pk"))
        pending = ManagedServiceAttachment.all_objects.filter(
            model_subscription=True, app_environment__registered_app_id__in=ids
        ).exclude(
            desired_enabled=False,
            subscription_status="revoked",
            applied_revision=F("desired_revision"),
            reconciled_at__isnull=False,
        )
        if pending.exists():
            raise ModelSubscriptionRetirementBlocked(REVOCATION_REQUIRED)
        yield locked


def retirement_guard(*, kind="app", field="input.id"):
    """Lock the retiring ancestor and consumer rows before invoking any resolver side effect."""

    def decorate(fn):
        sig = signature(fn)

        @wraps(fn)
        def wrapped(*args, **kwargs):
            from astrolift_graphql import failure
            from astrolift_identity.models import Organization, Project, Team
            from astrolift_registry.models import RegisteredApp
            from core.scope_args import read_arg
            from core.tenancy import get_current_tenant

            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            value = read_arg(bound.arguments, field)
            tenant = get_current_tenant()
            org_id = tenant.organization_id if tenant else None
            try:
                with transaction.atomic():
                    if kind == "app":
                        lookup = {"slug" if field.endswith("app_slug") else "guid": value}
                        rows = RegisteredApp.objects.filter(organization_id=org_id, **lookup)
                    else:
                        model = {"organization": Organization, "project": Project, "team": Team}[kind]
                        owners = model.objects.filter(guid=value)
                        owners = (
                            owners.filter(pk=org_id)
                            if kind == "organization"
                            else owners.filter(organization_id=org_id)
                        )
                        owner = owners.select_for_update().first()
                        if kind == "organization" and owner is not None:
                            from astrolift_services.models import ManagedService

                            if ManagedService.objects.filter(organization=owner).exists():
                                raise ModelSubscriptionRetirementBlocked(MODEL_CLEANUP_REQUIRED)
                        rows = (
                            RegisteredApp.all_objects.filter(**{f"{kind}_id": owner.pk})
                            if owner
                            else RegisteredApp.objects.none()
                        )
                    with locked_retirement_apps(rows):
                        return fn(*args, **kwargs)
            except ModelSubscriptionRetirementBlocked as exc:
                return failure("PRECONDITION", str(exc))

        return wrapped

    return decorate


def retirement_activity(fn):
    """Protect worker transitions and direct teardown invocations with the same row locks."""

    @wraps(fn)
    def wrapped(registered_app_id, *args, **kwargs):
        from astrolift_registry.models import RegisteredApp

        with locked_retirement_apps(RegisteredApp.all_objects.filter(pk=registered_app_id)):
            return fn(registered_app_id, *args, **kwargs)

    return wrapped
