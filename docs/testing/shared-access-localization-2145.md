# Shared access localization (#2145)

The first leaf translates `RoleSummary`, `PermissionMatrix`, `PermissionPicker`,
and `ScopePicker` in all eight supported locales. It adds only the
`shared.access.{role,matrix,permissionPicker,scopePicker,presentation}` objects;
existing access and other catalog messages remain identical.

Role names, descriptions and slugs remain as supplied. Known resource nouns,
role-summary guidance, permission counts, matrix columns/areas/states, filter and
empty-state copy, and scope navigation/selection guidance follow the locale.
Permission slugs, technical verbs, unknown resource IDs, scope names/IDs/slugs,
server errors and caller labels/reasons are preserved. Grouping, diffing, catalog
order, selections and outgoing callback arguments do not change.

The pure access model accepts optional presentation callbacks for permission
summaries and denied binding guidance. Default callers retain their existing
behavior. `access-copy.ts` exports typed localized presentation factories for
screen owners to opt into. Presentation callbacks cannot turn a denied binding
into an allowed one. Existing screen owners still need to localize their own
custom prose and adopt the factories where appropriate.

Scope load failures retain actual `Error.message` diagnostics. Unknown failures
store a marker so the fallback can follow a subsequent locale change. Existing
load targets, lazy expansion, retry and disabled-node keyboard/click checks remain
intact. These components explain supplied authorization state; this change adds
no grants or server authority.

Validation: 49 new real-Intl/ICU tests exercise all eight locales, literal metadata
and unknown IDs, canonical single/row/area permission changes, role expansion,
plural counts, constraint reasons, denied scope clicks/keyboard, retry, and
lazy-load errors. The 273-test affected run also covers the complete role/matrix/
permission/scope story groups, grant/access-explainer stories, stock access-model,
policy-model, grant previews and shared access helpers, principal pickers, form
state, accessibility and access-query regressions. TypeScript, ESLint and
formatting pass.

Access diagnostics, policy sentence/help/simulation and grant-flow prose are
separate follow-up leaves; this document does not claim their completion or
close the platform-wide #2145 issue.
