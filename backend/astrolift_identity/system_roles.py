"""
System role catalog.

These roles ship with the platform and are upserted by a one-shot
data migration on every deploy. They cannot be edited (``is_system=True``)
and are scoped per-level so the role chooser in the UI can filter to
the level the user is granting on.

Source of truth: ``specs/03`` §4.3.
"""

from __future__ import annotations

from core.permissions import Permission

# Common groupings, named once and reused below.
_READ_ALL = (
    Permission.ORG_READ,
    Permission.TEAM_READ,
    Permission.PROJECT_READ,
    Permission.APP_READ,
    Permission.APP_READ_LOGS,
    Permission.APP_READ_METRICS,
    Permission.SECRET_LIST,
    Permission.AUDIT_LOG_READ,
    # Auditors need to download the log for compliance reviews — issue
    # #433 makes the audit surface usable for SOC2 / similar audits.
    Permission.AUDIT_LOG_EXPORT,
    # App-log export (#483) — compliance + vendor-handoff bundle.
    # Auditors need the runtime log slice to pair with the audit log
    # for SOC2 evidence pulls; same gating shape as audit export.
    Permission.APP_LOG_EXPORT,
    # Forms surface (#453): readers see form definitions + their
    # submissions in the operator UI. Submit lives on a dedicated
    # role grant (see ``team_viewer`` / ``team_developer`` below) so
    # auditors don't accidentally submit on a published form.
    Permission.FORM_READ,
    # Skill registry: readers see the org's Skill + ToolDef catalog
    # (plus the platform-global skills). Write/import live on the
    # admin/developer roles below so auditors can't mutate the catalog.
    Permission.SKILL_READ,
)

_DEPLOY_OPS = (
    Permission.APP_DEPLOY,
    Permission.APP_ROLLBACK,
    Permission.APP_READ_LOGS,
    # App log export (#483) — SREs running an incident handoff or
    # vendor support ticket need to ship the runtime log slice; same
    # gating shape as the read-logs surface.
    Permission.APP_LOG_EXPORT,
    Permission.APP_READ_METRICS,
    Permission.SECRET_READ,
    Permission.SECRET_WRITE,
    Permission.SECRET_LIST,
    # Agent dispatch (spec 33, PR-1): a role that can deploy apps can
    # also dispatch a registered agent Workload (runAstroliftAgent).
    # Bundled with the deploy ops so the agents surface inherits the
    # same operator/developer reach as app deploys; org owner/admin
    # already get it via the full-enum comprehension.
    Permission.AGENT_DISPATCH,
)

# (slug, scope_level, name, description, permissions)
SYSTEM_ROLES: tuple[tuple[str, str, str, str, tuple[Permission, ...]], ...] = (
    (
        "org_owner",
        "ORG",
        "Organization Owner",
        "Everything in the organization, including delete + billing.",
        tuple(Permission),
    ),
    (
        "org_admin",
        "ORG",
        "Organization Admin",
        "Everything except org delete and billing changes.",
        tuple(p for p in Permission if p not in (Permission.ORG_DELETE, Permission.BILLING_UPDATE)),
    ),
    (
        "org_billing",
        "ORG",
        "Organization Billing",
        "Billing read + update only.",
        (Permission.BILLING_READ, Permission.BILLING_UPDATE),
    ),
    (
        "org_auditor",
        "ORG",
        "Organization Auditor",
        "All read permissions plus audit log access.",
        _READ_ALL,
    ),
    (
        "cluster_owner",
        "ORG",
        "Cluster Owner",
        "Register, update, unregister, and bring tenant clusters into management.",
        (
            Permission.CLUSTER_REGISTER,
            Permission.CLUSTER_UPDATE,
            Permission.CLUSTER_UNREGISTER,
            Permission.CLUSTER_MANAGE,
            Permission.PROVIDER_PLUGIN_READ,
            Permission.PROVIDER_PLUGIN_CONFIGURE,
        ),
    ),
    (
        "team_owner",
        "TEAM",
        "Team Owner",
        "Everything within the team, including delete.",
        (
            Permission.TEAM_READ,
            Permission.TEAM_UPDATE,
            Permission.TEAM_DELETE,
            Permission.TEAM_MANAGE_MEMBERS,
            Permission.PROJECT_READ,
            Permission.PROJECT_CREATE,
            Permission.PROJECT_UPDATE,
            Permission.PROJECT_DELETE,
            Permission.APP_READ,
            Permission.APP_CREATE,
            Permission.APP_UPDATE,
            Permission.APP_DELETE,
            *_DEPLOY_OPS,
            # Skill registry: team owners maintain the agent Skill +
            # ToolDef catalog and import skills from config repos.
            Permission.SKILL_READ,
            Permission.SKILL_WRITE,
            Permission.SKILL_IMPORT,
        ),
    ),
    (
        "team_admin",
        "TEAM",
        "Team Admin",
        "Manage projects, apps, members; cannot delete the team.",
        (
            Permission.TEAM_READ,
            Permission.TEAM_UPDATE,
            Permission.TEAM_MANAGE_MEMBERS,
            Permission.PROJECT_READ,
            Permission.PROJECT_CREATE,
            Permission.PROJECT_UPDATE,
            Permission.APP_READ,
            Permission.APP_CREATE,
            Permission.APP_UPDATE,
            *_DEPLOY_OPS,
            # Skill registry: team admins maintain the agent catalog.
            Permission.SKILL_READ,
            Permission.SKILL_WRITE,
            Permission.SKILL_IMPORT,
        ),
    ),
    (
        "team_developer",
        "TEAM",
        "Team Developer",
        "Read team resources; deploy + rollback + secrets on team apps; submit forms.",
        (
            Permission.TEAM_READ,
            Permission.PROJECT_READ,
            Permission.APP_READ,
            *_DEPLOY_OPS,
            # Forms #453: developers can submit on the team's forms.
            Permission.FORM_READ,
            Permission.FORM_SUBMIT,
            # Skill registry: developers read + import skills for the
            # agents they wire into their apps. Catalog edits (write)
            # stay on the admin roles.
            Permission.SKILL_READ,
            Permission.SKILL_IMPORT,
        ),
    ),
    (
        "team_viewer",
        "TEAM",
        "Team Viewer",
        "Read-only access to team resources; can read + submit forms.",
        (
            Permission.TEAM_READ,
            Permission.PROJECT_READ,
            Permission.APP_READ,
            Permission.APP_READ_LOGS,
            Permission.APP_READ_METRICS,
            # Forms #453: every org member should be able to read and
            # submit a published form (e.g. surveys, intake) even when
            # they have no write access to anything else.
            Permission.FORM_READ,
            Permission.FORM_SUBMIT,
        ),
    ),
    (
        "project_admin",
        "PROJECT",
        "Project Admin",
        "Manage apps + members within the project.",
        (
            Permission.PROJECT_READ,
            Permission.PROJECT_UPDATE,
            Permission.APP_READ,
            Permission.APP_CREATE,
            Permission.APP_UPDATE,
            Permission.APP_DELETE,
            *_DEPLOY_OPS,
        ),
    ),
    (
        "project_developer",
        "PROJECT",
        "Project Developer",
        "Deploy/rollback + secrets on project apps.",
        (Permission.PROJECT_READ, Permission.APP_READ, *_DEPLOY_OPS),
    ),
    (
        "project_viewer",
        "PROJECT",
        "Project Viewer",
        "Read-only access to project apps.",
        (
            Permission.PROJECT_READ,
            Permission.APP_READ,
            Permission.APP_READ_LOGS,
            Permission.APP_READ_METRICS,
        ),
    ),
    (
        "app_admin",
        "APP",
        "App Admin",
        "Manage app + app members.",
        (
            Permission.APP_READ,
            Permission.APP_UPDATE,
            Permission.APP_DELETE,
            *_DEPLOY_OPS,
        ),
    ),
    (
        "app_deployer",
        "APP",
        "App Deployer",
        "Deploy + rollback only.",
        (Permission.APP_READ, Permission.APP_DEPLOY, Permission.APP_ROLLBACK),
    ),
    (
        "app_developer",
        "APP",
        "App Developer",
        "Read app + logs/metrics + manage secrets.",
        (
            Permission.APP_READ,
            Permission.APP_READ_LOGS,
            Permission.APP_LOG_EXPORT,
            Permission.APP_READ_METRICS,
            Permission.SECRET_READ,
            Permission.SECRET_WRITE,
            Permission.SECRET_LIST,
        ),
    ),
    (
        "app_viewer",
        "APP",
        "App Viewer",
        "Read-only access to a single app.",
        (Permission.APP_READ, Permission.APP_READ_LOGS, Permission.APP_READ_METRICS),
    ),
    (
        "app_approver",
        "APP",
        "App Approver",
        "Approve deploys + secret changes only.",
        (
            Permission.APP_APPROVE_DEPLOY,
            Permission.APP_READ,
            # Secret-change approval (#488) — the approver role covers
            # both axes so a single grant lets a reviewer act on the
            # full approval queue without juggling two role grants.
            Permission.SECRET_APPROVE,
        ),
    ),
)
