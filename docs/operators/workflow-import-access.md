# Workflow import permissions

The import APIs keep their existing inputs. Neither flow nor TOML import takes a
project selector; a newly imported definition belongs to the active organization
and starts disabled. A selected team/project header does not change this owner.

| Operation | Permission scope |
|---|---|
| `previewWorkflowManifest` | `workflow.read` at any granted scope; parses only |
| `importWorkflowFlow`, including its preview mode | Organization `workflow.create` |
| New `importWorkflowManifest`, including its preview mode | Organization `workflow.create` |
| `importWorkflowManifest(replace: true, preview: false)`, same stage kinds | Existing definition's live project, otherwise organization `workflow.create`; existing owner `workflow.update` also required |
| Replacement with changed stage kinds | Organization `workflow.create` for the new organization version; existing owner `workflow.update` also required |

Compatible replacement preserves the existing definition and project owner.
Changed shape preserves the existing versioning behavior: it creates a new
organization definition, and repoints configured workflows only if their bindings
remain valid. Source-managed definitions retain their existing write refusal.
Missing, deleted, foreign or incoherent project/team ancestry resolves to the
explicit active organization scope, never the selected scope.

Bearer credentials must belong to the active organization and allow the declared
permission (`workflow:write` or `admin` for imports). Team-bound credentials can
replace a compatible definition owned by their own team's live project, subject
to their user's grants. They cannot create organization definitions, create an
organization version, or replace a sibling team's definition; an operator's wider
grants do not widen the token. Use the pure preview query for local read-only
manifest validation.

Replacement locks its definition and existing stages, rechecks both source and
destination grants, then writes. A stage-shape change after initial admission
cannot use a project-only create grant to create an organization version. A
concurrent new same-slug definition cannot become an unchecked replacement target.
No GraphQL signature, database migration or stored ownership change is introduced.
