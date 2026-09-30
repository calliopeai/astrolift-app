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

The diagnostics leaf translates `AccessExplainer` and `AccessCompare` under
`shared.access.diagnostics`: verdict sentences, known reasoning-step names,
pass/fail/informational labels, prompts, errors/retry, comparison headings and
empty states. Resolver details and unknown check IDs remain literal. Technical
binding labels retain their canonical `role@SCOPE:id` spelling; parsed roles,
scopes and IDs reach `bindingHref` unchanged. Supplied verdicts, neutral step
classification, comparison partitions, loading/error states and server diagnostics
retain their semantics. Permission strings and usernames are literal rich-text
values, including angle brackets and braces.

Its 41 new tests use all eight locales for ABAC denial, org-wide/superuser answers,
unknown checks and raw details, exact binding links, comparison partitions,
empty/loading/error/retry and rich-message contracts. The affected diagnostics
run passes 269 tests including all six related story groups. TypeScript, ESLint
and formatting pass.

The policy-guidance leaf translates `PolicyConditionHelp` and
`PolicySimulationPanel` under `shared.access.conditionHelp` and
`shared.access.simulation`. It preserves server-supplied condition catalog
labels/descriptions and translates the UI guidance about required attributes,
unknown kinds, catalog failures and loading. Actual condition/attribute IDs stay
literal. Unknown and unanswerable conditions remain described as denying access.

Simulation summaries localize holder/day/decision counts, outcomes, recorded vs
unrecorded history, retry and failure chrome. Actual holder links, scope labels,
action slugs, operator notes, server errors, timestamps and affected/recorded
partitions remain supplied. Unknown outcome IDs remain literal. The optional
`holderSentence` presentation callbacks leave its default pure API unchanged.

Its 40 new all-eight-locale tests cover every known attribute requirement, unknown
and failed catalogs, current holders vs recorded decisions, unknown-denied and
future outcomes, exact member links, refusal/retry, server diagnostics, literal
metadata and ICU count contracts. The affected policy-guidance run passes 133
tests with all policy/grant story states, pure access/policy/grant tests and
form/accessibility regressions. TypeScript, ESLint and formatting pass.

Policy sentence and grant-flow prose remain separate follow-up leaves; this
document does not claim their completion or close the platform-wide #2145 issue.
