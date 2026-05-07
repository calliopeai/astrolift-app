"""
GraphQL types for the identity / tenant-hierarchy models.

Each Strawberry type maps a model row to its public shape:
``id`` is the GUID (string), never the integer PK; tracking columns
are exposed for clients that build activity feeds; the FK chain is
expressed as nested types.
"""

from __future__ import annotations

import datetime as dt

import strawberry

from astrolift_graphql import GUID


@strawberry.type(name="AstroliftOrganization")
class OrganizationType:
    id: GUID
    slug: str
    name: str
    website: str
    scim_enabled: bool
    audit_log_retention_days: int
    preview_max_active_default: int
    log_retention_days_default: int
    created_at: dt.datetime
    updated_at: dt.datetime
    deleted_at: dt.datetime | None


@strawberry.type(name="AstroliftTeam")
class TeamType:
    id: GUID
    slug: str
    name: str
    organization: OrganizationType
    created_at: dt.datetime
    updated_at: dt.datetime
    deleted_at: dt.datetime | None


@strawberry.type(name="AstroliftProject")
class ProjectType:
    id: GUID
    slug: str
    name: str
    organization: OrganizationType
    team: TeamType
    created_at: dt.datetime
    updated_at: dt.datetime
    deleted_at: dt.datetime | None


def organization_to_type(org) -> OrganizationType:
    return OrganizationType(
        id=GUID(str(org.guid)),
        slug=org.slug,
        name=org.name,
        website=org.website,
        scim_enabled=org.scim_enabled,
        audit_log_retention_days=org.audit_log_retention_days,
        preview_max_active_default=org.preview_max_active_default,
        log_retention_days_default=org.log_retention_days_default,
        created_at=org.created_at,
        updated_at=org.updated_at,
        deleted_at=org.deleted_at,
    )


def team_to_type(team) -> TeamType:
    return TeamType(
        id=GUID(str(team.guid)),
        slug=team.slug,
        name=team.name,
        organization=organization_to_type(team.organization),
        created_at=team.created_at,
        updated_at=team.updated_at,
        deleted_at=team.deleted_at,
    )


def project_to_type(project) -> ProjectType:
    return ProjectType(
        id=GUID(str(project.guid)),
        slug=project.slug,
        name=project.name,
        organization=organization_to_type(project.organization),
        team=team_to_type(project.team),
        created_at=project.created_at,
        updated_at=project.updated_at,
        deleted_at=project.deleted_at,
    )
