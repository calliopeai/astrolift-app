"""A project's kind, derived from what its live apps declare (#2556).

A project has no kind field of its own. It is a ``factory`` when one live app
declares a factory kind in its manifest (the factory's controller; its role
agents need not declare anything). It is a ``brain`` when it has live apps and
every one declares a brain kind, so a brain page never hides an app that is not
a brain. Anything else is ``generic``. Only declared ``[app] kind`` values count,
never names, so a project's kind changes only through a manifest.
"""

from __future__ import annotations

from django.db.models import Case, Exists, OuterRef, Value, When

from astrolift_manifest.app_kinds import BRAIN_KINDS, FACTORY_KINDS

BRAIN = "brain"
FACTORY = "factory"
GENERIC = "generic"

ANNOTATION = "astrolift_project_kind"


def project_kind_expression() -> Case:
    """The kind of the project row in the enclosing query, as one SQL expression."""
    from astrolift_registry.models import RegisteredApp

    live_apps = RegisteredApp.objects.filter(project_id=OuterRef("pk"), deleted_at__isnull=True)
    return Case(
        When(Exists(live_apps.filter(app_kind__in=FACTORY_KINDS)), then=Value(FACTORY)),
        When(
            Exists(live_apps.filter(app_kind__in=BRAIN_KINDS))
            & ~Exists(live_apps.exclude(app_kind__in=BRAIN_KINDS)),
            then=Value(BRAIN),
        ),
        default=Value(GENERIC),
    )


def with_project_kind(qs):
    """``qs`` of projects with each row's kind on ``ANNOTATION``, in the same query."""
    return qs.annotate(**{ANNOTATION: project_kind_expression()})


def project_kind(project_guid: str) -> str:
    """One project's kind by guid; ``generic`` for a project that is gone."""
    from astrolift_identity.models import Project

    kind = (
        with_project_kind(Project.objects.filter(guid=project_guid))
        .values_list(ANNOTATION, flat=True)
        .first()
    )
    return kind or GENERIC
