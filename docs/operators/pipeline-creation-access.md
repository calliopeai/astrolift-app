# Pipeline creation access

`createPipeline` accepts a name, repository URL, default branch and TOML path.
It creates a pipeline in the active organization with no registered app.
Creation therefore requires the existing `app.update` permission at explicit
organization scope. A selected team or project cannot replace that owner;
app, project and team grants do not grant upward to the organization.

An organization bearer credential also requires its owner's organization
permission and the token permission ceiling. Team-bound credentials cannot
create organization pipelines, including credentials owned by organization
owners or platform operators. A credential from another organization cannot
use a role in the selected organization.

The mutation arguments, validation, repository and default-path normalization,
and response shapes are unchanged. This change adds no database migration or
GraphQL contract change. Existing app-associated pipelines continue to use
their app owner for other operations.

The PostgreSQL regression suite exercises real RoleBindings with sibling
selected scopes, organization grants, token ceilings and HTTP requests. It
also checks that rejected requests create no pipeline rows. The surface
guardrail has no remaining `#2115` exception.

The console exposes this contract at `/pipelines/new`, reached from the
Pipelines list. It sends only the four existing input fields, preserves a
failed draft for retry, and retains the created GUID if navigation fails after
commit. The create action requires `app.update` in the frontend permission set;
the API remains authoritative for its explicit organization scope.
