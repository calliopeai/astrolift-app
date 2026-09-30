"""Internal opt-in for authenticated reads of coherent persisted app history."""

from dataclasses import dataclass

from django.db.models import F, Q

from core.permissions import PermissionScope, ScopeKind


@dataclass(frozen=True, slots=True)
class HistoricalAppScope(PermissionScope):
    """Distinct from live scopes in permission caches; never an API input."""

    def __post_init__(self):
        if self.kind != ScopeKind.APP:
            raise ValueError("history requires an actual app scope")


def historical_app_owners(qs, org_id):
    return qs.filter(organization_id=org_id, organization__deleted_at__isnull=True).filter(
        Q(team_id__isnull=True) | Q(team__organization_id=org_id),
        Q(project_id__isnull=True)
        | Q(project__organization_id=org_id, project__team__organization_id=org_id),
        Q(team_id__isnull=True) | Q(project_id__isnull=True) | Q(team_id=F("project__team_id")),
    )
