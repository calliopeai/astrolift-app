"""
System role catalog: the stock roles every install carries (#1864).

These roles ship with the platform, and a resync data migration writes
them to every install whenever the catalogue changes. They cannot be
edited or deleted (``is_system=True``); an organization clones one into a
custom role to change it. Each is scoped to a level so the role chooser
can offer the roles that fit where a grant is made. The resolver does not
read ``scope_level``: a binding grants its role's permissions on its own
scope and everything below it.

The tiers #1864 names map onto the catalogue like this:

==================  ================================================================
Tier                Roles
==================  ================================================================
Org Owner           ``org_owner``
Org Admin           ``org_admin``
Team Admin          ``team_owner`` (can also delete the team), ``team_admin``
Project Maintainer  ``project_admin``; ``app_admin`` for a single app
Developer           ``team_developer``, ``project_developer``, ``app_developer``
Operator            ``team_operator``, ``project_operator``, ``app_deployer``
Viewer              ``org_viewer``, ``team_viewer``, ``project_viewer``, ``app_viewer``
Auditor             ``org_auditor``
==================  ================================================================

plus the single-purpose ``org_billing``, ``cluster_owner`` and
``app_approver``.

Every role's permission list is pinned slug by slug in
``astrolift_identity/tests/test_stock_roles_1864.py``. Org owner carries
the whole ``Permission`` enum and org admin all of it but two, so adding a
permission changes their lists too. Any change fails that test until the
pin is updated, and reaches an existing install only through a new resync
migration (copy ``0034``).

Source of truth for the original set: ``specs/03`` §4.3.
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
    Permission.AGENT_ENV_SPEC_READ,
    # Agents + Workflows modules (spec 34/36 Phase 0): the read-all /
    # auditor set sees the agent fleet + workflow surface. The agent
    # workload/run readers re-gate to ``agent.read``, so without this an
    # auditor who could see agents via the old ``app.read`` gate would
    # lose that visibility. Workflow CRUD is still staff-gated until
    # Phase 3, but the perm is granted now so the parallel-read promise
    # holds the moment those resolvers re-gate.
    Permission.AGENT_READ,
    Permission.WORKFLOW_READ,
)

# The part of ``_READ_ALL`` that is audit rather than reading: the audit log
# and the log export that pairs with it for compliance evidence. The org
# viewer holds ``_READ_ALL`` without it.
_AUDIT_GRANTS = (
    Permission.AUDIT_LOG_READ,
    Permission.AUDIT_LOG_EXPORT,
    Permission.APP_LOG_EXPORT,
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
    # Attaching to an agent box (#129) rides with dispatch: a role that
    # may start a box must be able to reach the box it started, or the
    # box is a node it pays for and cannot use.
    Permission.AGENT_BOX_ATTACH,
)

# Agents + Workflows module verbs (spec 34/36 Phase 0). Standalone from
# ``app.*`` so the modules are grantable mix-and-match, but seeded onto
# the existing roles to mirror each role's current app-perm level so
# **no existing user loses access**: a role that could see / create /
# run agents or workflows via ``app.*`` now also holds the parallel
# entity perm.

# Full management of both modules — for the admin-grade roles
# (team/project/app admins) that already hold full ``app.*`` CRUD.
_AGENT_FULL = (
    Permission.AGENT_READ,
    Permission.AGENT_CREATE,
    Permission.AGENT_UPDATE,
    Permission.AGENT_DELETE,
)
_WORKFLOW_FULL = (
    Permission.WORKFLOW_READ,
    Permission.WORKFLOW_CREATE,
    Permission.WORKFLOW_UPDATE,
    Permission.WORKFLOW_DELETE,
)
_AGENT_ENV_SPEC_FULL = (
    Permission.AGENT_ENV_SPEC_READ,
    Permission.AGENT_ENV_SPEC_CREATE,
    Permission.AGENT_ENV_SPEC_UPDATE,
    Permission.AGENT_ENV_SPEC_DELETE,
)

# Developer level — view+create+run, NOT manage (no update/delete).
# Mirrors the developer roles' app baseline, where developers hold deploy
# ops but not ``app.update``/``app.delete``: they can build + run, but
# editing/tearing down existing resources is admin-tier (spec 36 §0.2,
# consistent with §0.6.2). Keeps ``agents.canManage``/``workflows.canManage``
# false for a developer.
_AGENT_DEVELOPER = (
    Permission.AGENT_READ,
    Permission.AGENT_CREATE,
    Permission.AGENT_DISPATCH,
    Permission.AGENT_BOX_ATTACH,
)
_WORKFLOW_DEVELOPER = (
    Permission.WORKFLOW_READ,
    Permission.WORKFLOW_CREATE,
    Permission.WORKFLOW_TRIGGER,
)

# Operator tier (#1864): run, attach and cancel, no configuration. It runs
# apps (deploy, rollback), agents (dispatch) and workflows (trigger), and
# cancelling rides on those grants: ``agent.dispatch`` cancels an agent
# task, ``workflow.trigger`` a workflow run, ``app.deploy`` aborts a
# deployment. It attaches to agent boxes (the exec relay) and to running
# agent tasks (``agent_task.watch``, the VNC relay), and reads what it
# operates, logs and metrics included. It creates, edits and deletes
# nothing, holds no secret, and cannot steer a running task
# (``agent_task.send_input`` stays with org owners and admins).
_OPERATE = (
    Permission.APP_READ,
    Permission.APP_READ_LOGS,
    Permission.APP_LOG_EXPORT,
    Permission.APP_READ_METRICS,
    Permission.APP_DEPLOY,
    Permission.APP_ROLLBACK,
    Permission.AGENT_READ,
    Permission.AGENT_DISPATCH,
    Permission.AGENT_BOX_ATTACH,
    Permission.AGENT_TASK_WATCH,
    Permission.AGENT_ENV_SPEC_READ,
    Permission.WORKFLOW_READ,
    Permission.WORKFLOW_TRIGGER,
)

# Zentinelle defaults from the #1888 RBAC table. Org owner and org admin
# hold every Zentinelle permission through their full-enum comprehensions;
# the auditor and viewer defaults sit on those roles below. #1888 gives
# "Developer: usage and policy view on their projects". Every hands-on tier
# at team and project level carries it (owners, admins, developers and
# operators), so no admin sees less of their own scope than the developers
# they manage.
_ZENTINELLE_HANDS_ON = (
    Permission.ZENTINELLE_USAGE_VIEW,
    Permission.ZENTINELLE_POLICY_VIEW,
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
        (
            *_READ_ALL,
            # "Auditor: status, policy and audit view, and audit export" (#1888).
            Permission.ZENTINELLE_STATUS_VIEW,
            Permission.ZENTINELLE_POLICY_VIEW,
            Permission.ZENTINELLE_AUDIT_VIEW,
            Permission.ZENTINELLE_AUDIT_EXPORT,
        ),
    ),
    (
        "org_viewer",
        "ORG",
        "Organization Viewer",
        "Read-only access across the organization, without the audit log.",
        (
            # The auditor's read set without the audit grants, so the
            # auditor holds all of this plus audit (#1864: "Auditor (read
            # plus audit)"). Strictly read-only: unlike ``team_viewer`` it
            # does not submit forms.
            *(p for p in _READ_ALL if p not in _AUDIT_GRANTS),
            # "Viewer: status" (#1888). Status is org-wide, so no team,
            # project or app viewer carries it.
            Permission.ZENTINELLE_STATUS_VIEW,
        ),
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
            Permission.CLUSTER_USERS,
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
            Permission.APP_ACCESS,
            # Skill registry: team owners maintain the agent Skill +
            # ToolDef catalog and import skills from config repos.
            Permission.SKILL_READ,
            Permission.SKILL_WRITE,
            Permission.SKILL_IMPORT,
            *_AGENT_ENV_SPEC_FULL,
            # Agents + Workflows modules (Phase 0): full management,
            # mirroring the full app CRUD a team owner holds.
            *_AGENT_FULL,
            *_WORKFLOW_FULL,
            *_ZENTINELLE_HANDS_ON,
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
            Permission.AGENT_ENV_SPEC_READ,
            Permission.AGENT_ENV_SPEC_CREATE,
            Permission.AGENT_ENV_SPEC_UPDATE,
            # Agents + Workflows modules (Phase 0): full management.
            *_AGENT_FULL,
            *_WORKFLOW_FULL,
            *_ZENTINELLE_HANDS_ON,
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
            Permission.AGENT_ENV_SPEC_READ,
            # Agents + Workflows modules (Phase 0): view+create+run, NOT
            # manage — mirrors the developer's app baseline (deploy ops but
            # no app.update/delete); canManage stays false.
            *_AGENT_DEVELOPER,
            *_WORKFLOW_DEVELOPER,
            *_ZENTINELLE_HANDS_ON,
        ),
    ),
    (
        "team_operator",
        "TEAM",
        "Team Operator",
        "Run, attach to and cancel the team's apps, agents and workflows; no configuration.",
        (Permission.TEAM_READ, Permission.PROJECT_READ, *_OPERATE, *_ZENTINELLE_HANDS_ON),
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
            # Agents + Workflows modules (Phase 0): read-only, mirroring
            # the viewer's app.read.
            Permission.AGENT_READ,
            Permission.AGENT_ENV_SPEC_READ,
            Permission.WORKFLOW_READ,
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
            Permission.APP_ACCESS,
            *_AGENT_ENV_SPEC_FULL,
            # Agents + Workflows modules (Phase 0): full management.
            *_AGENT_FULL,
            *_WORKFLOW_FULL,
            *_ZENTINELLE_HANDS_ON,
        ),
    ),
    (
        "project_developer",
        "PROJECT",
        "Project Developer",
        "Deploy/rollback + secrets on project apps.",
        (
            Permission.PROJECT_READ,
            Permission.APP_READ,
            *_DEPLOY_OPS,
            Permission.AGENT_ENV_SPEC_READ,
            # Agents + Workflows modules (Phase 0): view+create+run, NOT
            # manage — mirrors the developer's app baseline (deploy ops but
            # no app.update/delete); canManage stays false.
            *_AGENT_DEVELOPER,
            *_WORKFLOW_DEVELOPER,
            *_ZENTINELLE_HANDS_ON,
        ),
    ),
    (
        "project_operator",
        "PROJECT",
        "Project Operator",
        "Run, attach to and cancel the project's apps, agents and workflows; no configuration.",
        (Permission.PROJECT_READ, *_OPERATE, *_ZENTINELLE_HANDS_ON),
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
            Permission.AGENT_ENV_SPEC_READ,
            # Agents + Workflows modules (Phase 0): read-only.
            Permission.AGENT_READ,
            Permission.WORKFLOW_READ,
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
            Permission.APP_ACCESS,
            Permission.AGENT_ENV_SPEC_READ,
            Permission.AGENT_ENV_SPEC_UPDATE,
            Permission.AGENT_ENV_SPEC_DELETE,
            # Agents + Workflows modules (Phase 0): full management.
            *_AGENT_FULL,
            *_WORKFLOW_FULL,
        ),
    ),
    (
        "app_deployer",
        "APP",
        "App Deployer",
        "Deploy + rollback only.",
        (
            Permission.APP_READ,
            Permission.APP_DEPLOY,
            Permission.APP_ROLLBACK,
            Permission.AGENT_ENV_SPEC_READ,
            # Agents + Workflows modules (Phase 0): run-only — a deployer
            # can dispatch agents + trigger workflows, mirroring its
            # app-deploy reach, but not create or delete them.
            Permission.AGENT_READ,
            Permission.AGENT_DISPATCH,
            Permission.AGENT_BOX_ATTACH,
            Permission.WORKFLOW_READ,
            Permission.WORKFLOW_TRIGGER,
        ),
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
            Permission.AGENT_ENV_SPEC_READ,
        ),
    ),
    (
        "app_viewer",
        "APP",
        "App Viewer",
        "Read-only access to a single app.",
        (
            Permission.APP_READ,
            Permission.APP_READ_LOGS,
            Permission.APP_READ_METRICS,
            Permission.AGENT_ENV_SPEC_READ,
            # Agents + Workflows modules (Phase 0): read-only.
            Permission.AGENT_READ,
            Permission.WORKFLOW_READ,
        ),
    ),
    (
        "app_approver",
        "APP",
        "App Approver",
        "Approve deploys + secret changes only.",
        (
            Permission.APP_APPROVE_DEPLOY,
            Permission.APP_READ,
            Permission.AGENT_ENV_SPEC_READ,
            # Secret-change approval (#488) — the approver role covers
            # both axes so a single grant lets a reviewer act on the
            # full approval queue without juggling two role grants.
            Permission.SECRET_APPROVE,
        ),
    ),
)
