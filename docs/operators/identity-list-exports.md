# Identity lists and complete CSV exports

The five identity pages retain cursor pagination (`after`/`limit`) and accept
additive numbered pagination (`page`/`pageSize`). Supplying `sort`, `page`, or
`pageSize` selects numbered pagination and ignores `after`; callers should use
one mode at a time. Pages start at one, default to 50 rows, and clamp their size
to 1–200. `totalCount` counts the authorized matching list before slicing.
Every declared sort has a primary-key tiebreak, including equal names and
missing activity. Descending keys have a `-` prefix; comma-separated keys support
stable multi-column ordering.

| Page | Filters | Sort keys; default |
| --- | --- | --- |
| `astroliftMembersPage` | `scopeKind`, `lifecycle`, `role`, `team`, `mine`, `admin`, `active` | `name`, `email`, `created`, `joined`, `lifecycle`, `lastActive`, `roles`; `name` |
| `astroliftInvitationsPage` | `status`, `role`, `invitedBy` | `name` (email), `email`, `created`, `expires`, `status`, `role`, `lastActive`; `-created` |
| `astroliftRoleBindingsPage` | `role`, `scopeKind`, `kind`, `holder`, `subject` | `name` (holder), `role`, `scope`, `created` (granted), `expires`, `lastActive`; `-created` |
| `astroliftRolesPage` | `isSystem`, `scopeLevel`, `createdBy` | `name`, `slug`, `created`, `scopeLevel`, `bindings`; `name` |
| `astroliftPoliciesPage` | `effect`, `scopeLevel`, `createdBy` | `name`, `slug`, `created`, `updated`, `effect`, `scopeLevel`; `name` |

Members' `role` and `admin` filters use live, unexpired, direct grants; `admin`
means organization member-management permission, including custom organization
roles. `mine` means sharing a team with the viewer. `createdBy` and `invitedBy`
accept `me`. Bindings' new `subject: ["me"]` is an alias for the existing
`holder: ["me"]`: it includes the viewer and their current organization's IdP
groups. Supplying both fields intersects them. Filters combine with AND;
values within one field combine with OR. Unknown subject/role values match no
rows. Roles remain this organization's live custom catalog plus the global
catalog; policies remain organization-owned. Roles and policies retain their
legacy `sortBy` cursor ordering, now declared in both frontend page documents.

Invitation `lastActiveAt` is nullable current-organization evidence. It is the
latest user audit event for a unique, live, active organization member whose
email matches the invitation case-insensitively. It is null for unmatched or
ambiguous emails, inactive/deleted users or memberships, and members without a
current-organization event. Global login timestamps and activity in other
organizations never supply this value. No invitation acceptance or activity is
inferred from an email match. Ordinary sorts enrich only the returned batch;
`lastActive` sorting evaluates the whole matching list before paging.

## CSV queries

`astroliftMembersCsv`, `astroliftInvitationsCsv`, and
`astroliftRoleBindingsCsv` return `AstroliftIdentityCsvExport`:
`filename`, `content`, `contentType`, and `rowCount`. They take the corresponding
page's `search`, `filter`, and `sort`, plus invitation `status` or binding
`appSlug`/`roleId` where applicable. They take no paging arguments. Unsupported
sorts and query/storage/authorization failures produce GraphQL errors; no
partial CSV payload is returned.

The complete authorized matching list is exported without a 200/500/5,000-row
cap. Member and invitation columns preserve the People CSV contract:
`Kind, Name, Email, Roles, Teams, Status, Last active, Joined`. Member roles
include every visible direct binding, avoiding the old blank role column and
page-size truncation. Bindings export `Subject, Email, Kind, Role, Scope, Source,
Granted, Expires, Last active, Binding ID`. Datetimes are ISO 8601, nulls are
empty cells, records use CRLF, and CSV quoting preserves embedded commas,
quotes and newlines. Formula-leading cells are escaped for spreadsheets.
Invitation claim tokens/hashes and identity-provider credentials are absent.

Member and invitation exports require current organization member-management
permission. Binding export applies actual owner permissions, current ABAC deny
chains and bearer ceilings before filtering or exporting; a team bearer cannot
borrow its user's organization grant. Foreign custom-role relations and
incoherent parent ownership are excluded. Coherent retired organization-owned
scopes remain available to organization administrators for grant cleanup,
matching the binding list contract. Each HTTP request checks current active
account and credential state, so revocation applies to later exports too.

People and Assignments request these complete exports with the displayed
filters and order, independently of the displayed page. The export action is
disabled while running; errors show feedback and leave no download. IdP groups
use their separate paged search contract and no longer silently stop at 5,000.

Rows and supporting role/team/activity lookups are processed in 200-row batches.
The complete CSV string is materialized in the GraphQL response and the browser
Blob: available memory, request timeout and deployment response limits still
apply to very large exports. Those limits fail the request rather than silently
truncating rows. This synchronous query does not provide a persisted snapshot
across concurrent identity writes or an asynchronous export job.
