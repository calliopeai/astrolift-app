"""Astrolift identity + tenant-hierarchy app.

Owns the canonical identity model (User, Organization, Team, Project,
IdentityProvider, OrgDomain) plus the RBAC primitives (Role,
RoleBinding, Member, Invitation, Policy, ApiToken) that the rest of
the platform binds against.

Coexists with the legacy ``organization`` app from the boilerworks
scaffold; new code targets ``astrolift_identity``.
"""

default_app_config = "astrolift_identity.apps.AstroliftIdentityConfig"
