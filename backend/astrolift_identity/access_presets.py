"""Easy-mode access presets over the stock roles (#2272).

A preset is a plain-language starting point (Viewer, App owner, ...) that
names the existing stock role it grants at each scope. It is not a role of
its own: assigning a preset creates an ordinary binding to that stock role,
so Easy and Advanced mode share one authorization engine and one set of
domain objects. What a preset grants and excludes is computed from the
stock role's actual permissions, never restated here.

Installation super-admin is outside every preset: no organization role, and
so no preset, can grant it.
"""

from __future__ import annotations

from dataclasses import dataclass

from astrolift_identity.system_roles import SYSTEM_ROLES
from core.permissions import Permission


@dataclass(frozen=True)
class Task:
    key: str
    label: str
    # Granted when the role holds every one of these.
    requires: tuple[Permission, ...]


TASKS: tuple[Task, ...] = (
    Task("browse_apps", "Browse apps and their environments", (Permission.APP_READ,)),
    Task(
        "read_logs_metrics", "Read logs and metrics", (Permission.APP_READ_LOGS, Permission.APP_READ_METRICS)
    ),
    Task("deploy_apps", "Deploy and roll back apps", (Permission.APP_DEPLOY,)),
    Task("approve_deploys", "Approve deploys that need approval", (Permission.APP_APPROVE_DEPLOY,)),
    Task("change_apps", "Change app settings and configuration", (Permission.APP_UPDATE,)),
    Task(
        "run_agents_workflows",
        "Run agents and workflows",
        (Permission.AGENT_DISPATCH, Permission.WORKFLOW_TRIGGER),
    ),
    Task(
        "manage_agents_workflows",
        "Create and change agents and workflows",
        (Permission.AGENT_UPDATE, Permission.WORKFLOW_UPDATE),
    ),
    Task("manage_access", "Manage members and access", (Permission.ORG_MANAGE_MEMBERS,)),
    Task("manage_org_settings", "Manage organization settings", (Permission.ORG_UPDATE,)),
)


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    summary: str
    # scope level -> stock role slug granted at that scope
    roles: tuple[tuple[str, str], ...]


PRESETS: tuple[Preset, ...] = (
    Preset(
        "viewer",
        "Viewer",
        "Sees apps, logs and metrics. Changes nothing.",
        (
            ("ORG", "org_viewer"),
            ("TEAM", "team_viewer"),
            ("PROJECT", "project_viewer"),
            ("APP", "app_viewer"),
        ),
    ),
    Preset(
        "deployer",
        "Deployer",
        "Ships and rolls back releases of existing apps.",
        (("TEAM", "team_operator"), ("PROJECT", "project_operator"), ("APP", "app_deployer")),
    ),
    Preset(
        "approver",
        "Approver",
        "Approves deploys that require approval.",
        (("APP", "app_approver"),),
    ),
    Preset(
        "app_owner",
        "App owner",
        "Runs an app end to end: settings, deploys and who can use it.",
        (("TEAM", "team_admin"), ("PROJECT", "project_admin"), ("APP", "app_admin")),
    ),
    Preset(
        "org_admin",
        "Organization administrator",
        "Everything in the organization except deleting it and billing. Never installation super-admin.",
        (("ORG", "org_admin"),),
    ),
)

_ROLE_PERMISSIONS = {slug: (scope, frozenset(perms)) for slug, scope, _name, _desc, perms in SYSTEM_ROLES}


def role_tasks(slug: str) -> dict[str, bool]:
    """Every task, granted or not, for one stock role."""
    _scope, permissions = _ROLE_PERMISSIONS[slug]
    return {task.key: all(p in permissions for p in task.requires) for task in TASKS}


def validate_presets() -> None:
    """Every preset names an existing stock role at the role's own scope."""
    for preset in PRESETS:
        for scope, slug in preset.roles:
            if slug not in _ROLE_PERMISSIONS:
                raise ValueError(f"preset {preset.key}: no stock role {slug!r}")
            if _ROLE_PERMISSIONS[slug][0] != scope:
                raise ValueError(
                    f"preset {preset.key}: {slug} is a {_ROLE_PERMISSIONS[slug][0]} role, not {scope}"
                )
