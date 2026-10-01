# Frontend translations audit (#2145)

Keep all eight locales. English is the canonical key shape; each locale has its
own complete translations. `i18n/request.ts` loads only the selected catalogue,
so missing keys do not receive an English deep-merge fallback. Preserve ICU
arguments, plural/select branches and rich-text tags; identifiers, brand names,
code and user/server-provided content remain data.

The initial integration snapshot `e420ab4f` has 2,359 English leaf keys. Each
non-English catalogue misses the same 224 keys and retains 8 or 11 older extra
keys. Roughly 1,454–1,603 values match English; this includes technical names and
copied seed text, so equality alone does not establish untranslated copy.

| Item | Current surface / behavior                             | State                                                    |
| ---- | ------------------------------------------------------ | -------------------------------------------------------- |
| 1    | Audit feed, metrics panels, feature flags and feedback | Pending                                                  |
| 2    | Reprovision unknown-state lookup                       | Pending                                                  |
| 3    | Activity timeline                                      | Pending                                                  |
| 4    | Apps list actions, sorting, empty/filter states        | Pending                                                  |
| 5    | App frame, quick links and deployment strip            | Pending                                                  |
| 6    | Agent config entries                                   | Pending                                                  |
| 7    | Manifest editor and feedback                           | Pending                                                  |
| 8    | Domains, certificates, redirects and sheets            | Pending                                                  |
| 9    | Security thresholds and app previews                   | Pending                                                  |
| 10   | Secret and deploy-token screens                        | Pending                                                  |
| 11   | Deployment actions, comparisons and details            | Pending                                                  |
| 12   | Scaling feedback passes ICU replica/error values       | Fixed, eight-locale Apollo/React regression              |
| 13   | Settings, environment overrides and members            | Pending                                                  |
| 14   | Secret proposals and pending gates                     | Pending                                                  |
| 15   | Environment actions and feedback                       | Pending                                                  |
| 16   | Current `RunsScreen` time column uses `useFormatters`  | Existing implementation; obsolete `TasksScreen` removed  |
| 17   | Install-wide previews list                             | Pending                                                  |
| 18   | Project operation timestamps and status sentence       | Fixed, configured-locale/time-zone screen regression     |
| 19   | API key Created, Expires and Last used cells           | Fixed, configured-locale/time-zone screen regression     |
| 20   | Teams and API key copy                                 | Pending                                                  |
| 21   | Current workflow runs/instances use `useFormatters`    | Existing implementation; reported legacy screens removed |

The older `WorkflowTimeline` showcase remains a separate parked playground
surface and is not the active workflow run list. New real playground copy also
uses all locales. Catalogue completion and the remaining source-copy work are
required before this issue closes.

## People list presentation

`MembersScreen` localizes headings, row actions, invitation confirmations, empty
states, CSV export counts, lifecycle/status labels and group counts in all eight
locales. Its translated definition keeps role/team labels, filter tokens, sorts,
pages and routes intact; unknown metadata stays literal. Last-active ages use
the locale and request clock, and absolute dates use the configured time zone.
Invalid last-active timestamps remain literal instead of breaking the list.

`people-translations.test.tsx` covers all eight catalogs, ICU contracts,
current permission gates, exact identity/destination preservation, CSV callbacks,
refusal retention and hydration. Existing People model tests retain the server
query contracts; portable stories exercise all declared list states. The wider
Members item remains incomplete until the connected legacy grant dialog body and
feedback are translated and verified.

Connected People invitation/anonymization mutations and CSV failures now have
all-eight-locale feedback. Successful writes retain their accepted result through
failed list reads; a rejected envelope does not trigger the post-write refresh.
Original diagnostics stay literal, clipboard success awaits the browser API, and
self-anonymization honors the actual `requiresLogout` signal. The CSV helper's
optional fallback preserves other callers' behavior.

The anonymization dialog now states the connected mutation's actual effects:
account/profile identity replacement, sign-in disabling and membership
deactivation. It explicitly retains the historical-audit/stored-binding
limitations tracked in [#2220](https://github.com/calliopeai/astrolift-app/issues/2220).
Its irreversible acknowledgement belongs to the current account, so switching
targets disables confirmation again. Real HttpLink tests validate current SDL,
exact mutation inputs, accepted/refused/missing/transport outcomes, failed refresh,
logout and clipboard completion in every locale. Dialog/ICU tests and all declared
anonymization stories pass. This does not establish historical production PII
scrubbing or role-binding deletion; no live user mutation was run.

## Shared list and table chrome

`ListPage`, `FilterBar`, `ViewToggle`, `NewRowsPill`, `ListSummary`, the DataTable
controls and shared empty/help and panel-error fallbacks use `shared.list` and
`shared.table`. The 57 message keys have actual translations in all eight
locales. DataTable cursor controls reuse `shared.pagination` without changing
that namespace. Rich messages permit translated word order; ICU plural rules
and counts follow the selected locale. Non-English caller labels keep their
capitalization. English retains its existing lowercase noun convention.

Field, column, view and option labels, view notes, explicit empty-state copy,
search/help overrides, row identity and server messages remain caller data.
Screens must supply their own translated definitions. The shared sort summary
names the actual selected column and direction rather than assuming a default
descending sort is chronological. Sort IDs/directions, filter IDs/values, search
payloads, selected row IDs, paging and navigation destinations are unchanged.

Regression coverage uses real next-intl providers in all eight locales, verifies
ICU argument/tag parity, and exercises shared filter, free-value, sort, column,
view, selection and pagination behavior. It distinguishes filtered versus
view-empty results and checks untouched server errors in table/card renderers,
locale counts, unknown summary totals and capped live-row notices. German and
Japanese portable stories cover ready, loading, error and empty states at
768px and with long identifiers. This closes the shared chrome slice; the
screen-specific items in the issue remain independently tracked.

Existing behavior tests use `test/render-with-intl.tsx` to retain the real app
locale context around their Apollo/permissions wrappers. Legacy screen-specific
translation mocks delegate shared namespaces to next-intl, preserving their
existing screen assertions while exercising actual shared ICU messages.

## Shared feeds

`Feed` uses the selected locale and configured timezone for both calendar-day
grouping and headings (`shared.feed`, nine keys in every locale). Today and
yesterday compare local calendar dates, including DST transitions, rather than
subtracting 24 hours from an instant. Unknown dates remain explicitly unknown.
The pure `groupItems`/`dayLabel` helpers keep their optional legacy presentation
contract; rendered feeds always supply the real next-intl context. Labels, item
content, caller grouping labels, explicit state/load-button overrides and server
diagnostics remain caller data. Cursor admission and scroll retention are
unchanged. Locale interaction tests cover all eight languages, a New York
midnight/DST boundary, original failures and bounded load callbacks; French and
Japanese stories exercise the shared frame.

## Agent secret values and attached bundles

The value list, legacy value dialog, bundle consumers and their feedback use
120 translated keys under `agentSecrets.values`, `agentSecrets.bundles` and
`agentSecrets.feedback` in every locale. The existing `agentSecrets.source`
picker and access-boundary messages are preserved. Copy identifies the
explicitly selected environment recipe rather than implying that a secret
recipe is a persisted binding to an agent. Bundle attachments affect that
recipe's default environment; direct references override matching variables.

The English static list definition remains available to parser tests; rendered
consumers use its translated presentation factory. Field IDs, filter values,
sort/paging callbacks, provider URIs, namespace boundaries, variable and bundle
keys, permissions, mutation inputs and exact server diagnostics remain intact.
Failed status/catalog/attachment reads display the original diagnostic and an
actual read retry. Initial attachment loading blocks attach forms, so an
unknown attachment list cannot masquerade as empty. Cached data is retained
while refreshing. These changes do not infer an agent-to-recipe association or
change the backend/source-verification contract.

All-eight-locale React/Apollo regressions verify ICU argument/tag parity,
actual selected-recipe mutation inputs, failed-write drafts, exact destructive
targets, untranslated provider diagnostics, and read/loading/empty distinctions.
Portable German/French/Japanese stories cover ready, long, loading and failed
states.

The bounded feedback follow-up reuses `refetchAfterMutation` with an optional
localized warning; existing callers retain the original default. All eleven
value/reference and bundle/key write paths preserve a committed result if
refresh fails, release pending admission and warn to refresh rather than repeat
the write. Boolean save callbacks return true after a successful write, so
editors can clear committed drafts. Twenty real Apollo regressions exercise
every path and all eight locales, suppress concurrent duplicate submissions,
and prove a read retry never resubmits the mutation. A rendered French editor
clears its committed plaintext draft after a read retry recovers the view.
Apollo's callback cache-diff argument retains the existing generic warning;
it is never interpreted as translated copy. Actual write refusals
retain the original provider diagnostic and unsuccessful callback result.

## Home layouts

Home's layout menu, first-sign-in question, picker and no-access/placeholder
states use the `home` namespace in all eight locales. The English registries
remain compatible with model tests and fixtures; presentation helpers translate
their canonical titles/descriptions while preserving explicit caller overrides.
Layout and panel keys, ordering, permissions, module checks, destination links
and saved preference values remain unchanged. First-sign-in still mounts no
data panels until the viewer chooses a layout, and lost access still selects
the same permitted fallback. All-eight-locale interaction and ICU regressions
cover these boundaries; German and Japanese 768px stories cover the picker and
operator layout.

## Home panels

Home's message tree covers its four layouts and seventeen registered panel
presentations. Home-owned titles, default states, actions,
approval sentences and feedback, known status/severity/health labels, chart
legends and accessible summaries use those translations. Numbers, money,
percentages, metric units, durations and relative timestamps use real locale
formatters. Unknown status IDs, resource names, image identifiers, provider
labels, keys, destination links, original diagnostics and explicit caller
presentation overrides are preserved. Invalid timestamps remain explicitly
unknown. The KPI model retains English default formatting for pure fixtures;
the actual hook supplies locale formatters from its observed numeric sources.

The My agents panel still joins the authorized fleet to authorized apps on
which the viewer holds a role. Its new note describes that actual read rather
than reusing the current agent list's different registered-by-viewer ownership
note. The My apps hook continues to use `apps.list.views.mineNote` unchanged.
All query documents, arguments, permission/module admission, result counts,
ordering, source limits, cursor/paging callbacks and saved preferences remain
unchanged. No new backend or tenant query is introduced. Missing forecast and
budget observations show a dash, while observed zero remains a real zero.
The Prometheus reason states remain distinct; server failures retain their
original message and read retries remain read retries.

All-eight-locale React/Apollo checks cover the actual approval target, refused
approval feedback and retry, server query variables and skipped sources,
observed counts/meter/rates, unknown states and preserved diagnostics, fallback
sentences and relative times. Portable and Chromium stories cover all Home
views, including translated 768px and long states.

ActivityFeed now uses the configured next-intl locale and request clock for its
frame and relative timestamps. The shared Feed supplies localized day headings
in the request time zone. Invalid event timestamps, actors, action values,
target labels, original server errors and drill-down destinations stay intact.
The Load older callback remains the retry for an available older page; the
Retry callback appears when there is no older page. Clock ticks do not fetch.
All-eight-locale tests cover loading, empty, paging and original error states,
fixed-clock server rendering and hydration, and locale/time-zone changes.

FleetView's shared visualization chrome remains a separate follow-up boundary;
this bounded port does not claim that issue #2145 is closed.

## Home layout settings

The connected Settings › Home consumer translates its heading, available layout
presentation, access-empty state and reset action in all eight locales. Explicit
caller layout labels and actual saved/default keys remain data. The description
reflects account synchronization with an offline browser copy, rather than
claiming browser-only persistence. Choosing a layout still saves immediately;
reset still sends an explicit null with the answered flag.

The preferences bridge translates the missing-message refusal fallback only for
Home layout writes. Original server diagnostics and unrelated preference behavior
remain unchanged. Actual Apollo/consumer tests verify successful saves and resets,
server refusals restoring the last account answer, transport failures retaining
the browser choice, first-read loading and offline fallback, locale changes without
resubmission, and narrowed access without automatic preference rewrites. German
768px and Japanese access-empty portable stories exercise the same consumer.

## Team list and create/edit/delete

The connected team list, creation and editing sheets, slug-status guidance,
retirement confirmation and mutation feedback use genuine translations in all
eight locales. The localized list factory preserves view/filter/sort/page IDs and
query arguments; known shared breadcrumb translation remains a separately frozen
composition dependency. Names, slugs, descriptions, organization/team IDs and
original server messages remain literal. Existing locale/time-zone date formatters
remain in place. The retirement copy makes no promise of descendant visibility or
automatic reassignment.

Creation and editing retain refused drafts. The actual Apollo HTTP path tests
cover accepted writes, structured refusals, diagnostic-free failure envelopes,
transport failures and failed post-write refreshes. The shared refresh helper
keeps committed create/edit/delete outcomes successful and warns only about the
read, so closing a successful sheet never encourages repeating its write.
No-organization, invalid-slug and read-only states preserve the existing gates.
Project slug hints retain their existing default presentation; Teams supplies
localized labels through an optional presentation argument.

Team detail, members and access panels remain outside this list/create/edit/delete
leaf. API key screens and scope catalogue presentation are the next bounded slice;
this leaf does not claim complete #2145 coverage.

## API keys and reviewed scope presentation

The connected API key list, creation/reveal/revocation flow, metadata detail and
scope picker use genuine translations in all eight locales. `apiKeys` is additive;
all previously existing locale values remain unchanged. The localized list factory
preserves query/view/filter/cursor identities and explicitly retains the newest-100
client-narrowing disclosure. Known stock scope labels/descriptions are localized
only when both their technical identity and exact reviewed server text match.
Custom, changed or future metadata, permission IDs, scope strings, user/token/team
names, suffixes, user agents and server diagnostics remain literal. Presets still
filter against the actual server `available` values; unavailable selected scopes
remain removable. This presentation supplies no permission authority.

Creation keeps denied or unavailable drafts. Accepted create/revoke replies retain
their committed outcome after a failed refresh; rejected envelopes request no
refresh. Browser copy feedback awaits the clipboard result, catches denied or
unavailable APIs, and retains the one-time value for manual copy. Dismissal remains
explicit. Metadata transport errors have a translated read-error frame and actual
retry, instead of an invented not-found result; cached metadata stays visible on a
failed refresh. Token detail uses opt-in shared-shell copy and locale/time-zone
timestamps; other entity-detail consumers retain their prior defaults. Status
presentation uses the request clock and does not establish token authority.

Focused tests use the real Apollo HttpLink for creation, revocation and retry,
including all-eight refused, diagnostic-free, transport and refresh failures,
clipboard completion/denial/unavailability, translated retained confirmation,
locale changes with draft preservation and keyboard submission, server-directed
scope admission, unknown metadata, ICU argument/tag parity, request-clock hydration
and configured time zones. Portable token/detail/picker stories include German,
French and Japanese states. Existing shared mutation-feedback and date regressions
remain part of the affected gate. This leaf does not close other #2145 consumers,
add a singular token API, change server permissions or broaden client-side list
filtering beyond its disclosed current contract.

### Team refusal and refresh refinement

Create/edit/delete refetch requests now depend on the actual `ok` envelope. Real
Apollo HTTP regressions combine a refused mutation with a read that would fail:
no read refresh occurs, no refresh warning or success is shown, and the original
refusal, edit/create draft or existing list row remains intact. Accepted-write
refresh-failure behavior is preserved through the same shared helper.

## Connected legacy role-grant sheet

The `GrantRoleDialog`/`GrantRoleSheet` and `useGrantRole` used by the actual
administration assignments tab now use additive `shared.access.legacyGrantRole`
copy in all eight locales. The copy asks for the numeric ID of an existing member
of the active organization, matching the real backend membership contract; it
makes no future SCIM/search promise. Custom/system role names, scope tokens,
resource identifiers, member labels and backend diagnostics remain literal.

Role and organization/team/project target reads distinguish loading, unconfirmed
and failed results from a known empty list. The actual assignments caller forwards
role read state and retry; a failed role read retains its original diagnostic,
blocks cached selections, and refreshes the real role query on retry. Known empty
roles remain distinct from unknown roles. Failed scope reads offer an actual retry;
unknown organization facts and withdrawn current selections remain disabled.
This legacy picker has no APP source, which it discloses explicitly rather than
inventing a target or default. Existing parent permission gating is unchanged;
backend destination-scope `org.manage_members`, grant ceilings and current actor
checks remain authoritative. The sheet makes no new client permission assertion.

Real HttpLink tests cover all-eight grants/refusals/no-diagnostic/transport/read
refresh failures with exact IDs and existing active page variables. Accepted
writes retain success if their read refresh fails; refusals keep drafts and issue
no refresh or warning. The actual assignments consumer proves Cancel does no read
and a committed grant refreshes its active page exactly once. Its former
unconditional close refetch was redundant and could leave an unhandled read
failure after commit. Locale/context changes and unsupported/unknown/read-error
sources, custom metadata, ICU arguments/tags and portable stories are included.
The legacy flat scope sources are preserved; this leaf does not add pagination,
a new member picker, APP support or claim complete #2145 coverage. Team detail,
member and access presentation remains the next independently bounded audit.

## Connected Team detail and members

Team detail and members now use real all-eight `teams.detail`, `teams.members`
copy and translated `lists.teamMembersBulk` instead of copied English. Human
breadcrumb/tab/list labels derive from the existing definitions; hrefs, query
keys, scope tokens, selected IDs, role metadata and unknown lifecycle values
remain literal. Membership guidance follows the actual role-at-team contract,
including organization-level roles the existing picker already permits; it
makes no nonexistent `/roles` navigation promise.

The actual detail/member hooks distinguish failed and unconfirmed reads from
known missing/empty results, expose real retries, and preserve cached identities
with visible read failure. Role loading, unknown, error and confirmed empty
states stay distinct in the assignment dialog. The existing team-management
check controls actions; withdrawing it disables an open assignment without
silently discarding its draft. Backend scope/grant checks remain authoritative.
A changed team identity remounts that team's member panel rather than carrying
another team's selected-member draft into a new target.

Bulk assignment retains exact typed input and existing partial/idempotent result
semantics. Only accepted replies refresh the same team-member query. A refresh
failure warns that data could not be refreshed while keeping the committed
result; a refused or failed write keeps the selection and original diagnostic.
An accepted reply without an aggregate summary is labeled accepted with no
fabricated counts. Real HttpLink tests exercise these boundaries, unknown/read
errors and actual retries, click/keyboard confirmation, current permissions,
all-eight ICU shape and SSR/hydration with explicit request timezone dates.
Portable Team detail/member stories include translated and failed-read states.

This leaf retains the legacy capped 500-row member source and its existing
client list operations; it does not claim a new server pagination contract or
complete access translation. The shared EntityAccessPanel/removal hook and Team
reach summary are the separate next leaf. Other principal-page callers retain
existing presentation defaults; Team opts into translated recovery labels and
cached-refresh error display. Root's frozen Members/anonymization source is
untouched.

## Connected Team access and grant removal

Team reach now uses genuine all-eight `teams.access` copy and the actual project
read/retry. Cached project failures stay visible beside literal project rows;
an unconfirmed reach read supplies no confirmed count. Scope guidance explains
that a team's projects/apps are in its scope while role and policy still decide
access. The route supplies a translated team subject without rewriting the team
slug or the exact target GUID.

The connected shared EntityAccessPanel/list/removal hook uses additive
`shared.access.entityPanel` copy: columns/search/source nouns, group counts,
expiry fallback, empty guidance, inherited/group blast radius, confirm and
feedback. Custom/system role metadata, scope identifiers, technical share levels,
unknown source labels and request arguments remain literal. Known source labels
are translated through an exact enum-key mapping; unknown sources are not
misrepresented as app shares. The optional group-detail presentation callback
preserves the pure helper's existing behavior for other callers.

Refused revoke/mapping replies trigger no refetch and preserve the raw error and
open confirmation. Accepted replies refresh active queries with their existing
variables; failed refreshes warn while keeping the committed removal successful.
Neither missing/null reads nor transport errors appear as a healthy empty list.
Retries execute the actual current source query and consume its rejection while
the query state retains the diagnostic. Query.previousData is no longer carried
across target changes: current query cache can remain visible during a same-target
refresh, but the new target cannot display or remove the prior target's rows.
An open confirm requires its row to be present in the current result and the
existing management decision; unknown/nonremovable sources do not invent a
role-binding write. Backend target/grant checks remain authoritative.

Real HttpLink tests cover both actual removal operations in every locale with
click/keyboard confirmation, complete inherited/group guidance, exact mutation
IDs and current active refetch variables, transport/fallback/refusal/committed
refresh outcomes, deferred target changes, null/failed reads and real retries.
Locale changes retain open target identity. All-eight ICU and SSR/hydration
checks preserve raw role/group/project metadata and explicit request-timezone
dates. Portable shared/Team access stories cover translated, failure and removal
states. Team detail/member and existing feedback/polling checks remain green.

Team list/create/edit/delete, detail, member assignment and this access surface
now have connected locale coverage; #2145 remains open for other uncompleted
surfaces. Existing capped/flat team/member/project read contracts are unchanged;
this work adds no pagination API, authority promise, schema or backend change.
Other shared-panel callers' supplied literal subject descriptions and other
principal screens remain their own translation boundaries. Root's frozen
Members/anonymization source and current release are untouched.

## Cluster settings heartbeat agent and source recovery

The actual cluster settings Keep-alive agent card now uses genuine all-eight
`clusterSettings.agent` copy for issue/rotate/deploy, one-time key handling,
clipboard feedback and install instructions. Its interval uses request-locale
number and singular/plural formatting with unchanged technical namespace and
Secret names. Cluster GUIDs, heartbeat URLs, raw key values, the existing kubectl
snippet and backend diagnostics remain literal. This is the existing cluster
heartbeat agent; it adds no Agent Host Protocol work.

Issue and deploy refresh the active GetCluster query only after an accepted
mutation envelope, retaining its existing variables. Accepted replies survive a
failed refresh with a translated warning; rejected replies do not refresh or
announce a successful write. Transport failures are handled without discarding
an already issued key. Deployment acceptance does not claim an observed
heartbeat or guaranteed connection. Clipboard success is displayed only after
the browser confirms the write, with the existing temporary copied label;
clipboard refusal keeps the key/snippet available for manual copying.

Transient key and copy feedback belong to the current cluster/issued response.
Cluster changes clear the key, and a delayed old response cannot resurrect it
when the viewer returns to that cluster. No new credential persistence, query,
authority gate, backend contract or cloud operation is introduced.

The connected existing settings read has a narrow optional error/retry seam.
Initial errors and unknown/mismatched target results show unavailable, not a
healthy not-found state. A confirmed null result remains visibly not-found or
inaccessible. Same-target cached observations remain visible with the raw
refresh error and an actual Retry action. The exact GetCluster variables,
cache-and-network read, management polling and permission decisions are
preserved. The existing header accepts an optional empty-state title without
changing other callers' presentation.

Actual Apollo HttpLink tests cover both credential operations in all eight
locales, raw refusal/transport/fallback outcomes, exact active refresh inputs,
accepted-write/failed-read and refused-write/no-read combinations, prior-key
preservation, literal clipboard payloads and completion races. The actual
ClusterSettingsClient route proves initial failure-to-retry, cached failure-to-
retry, null/unknown distinctions and new-target pending admission. ICU argument,
rich-tag and plural-option parity, locale changes, SSR/hydration and all existing
settings portable stories are checked with the real locale context.

Focused validation on this exact source: 198 checks pass across the agent
translation/route and portable-story modules plus existing cluster preflight,
connectivity and ingress-auth regressions. TypeScript, scoped ESLint (zero
warnings), source/catalogue Prettier and git diff checks pass. Every pre-existing
catalogue value matches the signed 1ed90e1d base exactly; only the two owned
clusterSettings subtrees are added. No broad frontend/backend suite was run.

This is a bounded #2145 leaf. Other cluster header/tab/lifecycle/connection,
bootstrap, central/ingress-auth and auth-user presentation and feedback remain
separate concrete translation boundaries. Their source/card/query contracts are
unchanged; this leaf adds no paging/capacity guarantee and does not close #2145.

## Cluster settings central OIDC authentication

The connected CentralAuthView/useCentralAuth form and actual CentralAuthCard
now use genuine all-eight `clusterSettings.centralAuth` presentation: labels,
write-only hints, secret-presence badges, setup/edit, provider callback guidance
and save/fallback/refresh feedback. The existing shared SettingsSection supplies
localized Save/Saving/Cancel. The callback template and issuer/JWKS paths remain
literal technical values. Missing presence flags are visibly unconfirmed rather
than fabricated unset. Existing real boolean flags preserve their meaning.

The exact updateTenantCluster input and existing config preservation are
unchanged: public issuer/logout/upstream metadata survives, the required fields
are trimmed as before, optional blank JWKS is removed, `_set` flags are not sent,
and a blank secret omits the secret key so the backend retains the stored value.
Only newly typed secrets enter the write. The client does not retrieve stored
secrets or persist its draft to browser storage. Cancel clears the typed secret
and discards the current draft.

Only accepted update envelopes refresh active GetCluster queries with their
existing variables. Refusals retain the raw diagnostic and reviewed draft and
trigger no refresh or successful-write warning. Transport errors are handled;
a committed write still reports saved and clears the typed secret if its refresh
fails, while the source banner exposes the raw read error and actual retry.

Local form state belongs to its cluster GUID and confirmed OIDC source presence.
A new cluster or confirmed source withdrawal clears the prior draft and secret;
an old promise cannot close/clear a newer editor. A locale change and same-target
failed/refused operation retain the current reviewed values. The actual existing
Restricted wrapper still decides read-only/hide behavior; withdrawal remounts a
read-only card, preventing reopening/writes without a new authority rule.

The help accurately describes first-party parent-zone shared sign-in and
independent custom-domain authentication, following central-auth-envoy.md.
Saving metadata is not proof of an IdP registration, current edge rollout,
provider capability or authentication readiness. This form does not enumerate
provider resources or create a new paging/capacity promise. Existing backend
permissions and mutation authority remain decisive; no backend/schema/input or
policy gate changes are introduced.

Focused current-source proof: 155 checks pass across real Apollo HttpLink
ClusterSettingsClient journeys, all-eight ICU/rich/SSR hydration and portable
central/settings stories, plus the existing central/ingress-class card regressions.
It covers keyboard save, exact public/secret payloads, raw refusal/transport and
fallback, accepted-write/failed-read recovery, source withdrawal/failed/null reads,
Cancel/no-write, cached target changes, delayed actual prior-target HTTP replies,
existing permission withdrawal, unknown secret flags and locale changes.
TypeScript, scoped ESLint (zero warnings), source/catalogue formatting and diff
checks pass. All pre-existing catalogue values exactly match the signed 4050b17a
base; only the owned centralAuth subtree is added.

This bounded central-auth form is complete, while #2145 remains open. The sibling
ingress-class selector/hook, Cognito ingress-auth card/pickers, auth-user list,
other cluster header/tab/connection/lifecycle/bootstrap copy and their outcomes
remain concrete subsequent boundaries. Those forms and their capability limits
are unchanged; this leaf does not enable unsupported provider authentication.

## Connected cluster ingress-class review and outcomes

The ingress-class card and `useIngressClass` use the additive
`clusterSettings.ingressClass` subtree with 20 genuine message leaves in all
eight catalogues. Known class labels/guidance, gate warnings, manifest opt-in,
confirmation and mutation/refresh feedback are translated. Technical class
values and unknown future class identifiers remain literal; query variables,
update inputs and existing permission decisions are unchanged. No credential
configuration, backend/schema or provider-admission contract changes are made.

Reviews are scoped to the current cluster GUID, observed class and authentication
source facts. Switching the target or withdrawing confirmed source facts clears
the older selection, opt-in and dialog; late completion cannot close a newer
review or carry the old opt-in to a different cluster. A locale change preserves
the reviewed technical target. The ALB advisory now treats an empty dictionary
as unconfigured, matching the actual backend gate check. It does not introduce
a new client authority rule.

Only accepted update envelopes refresh active GetCluster queries with their
existing variables. Original refusal/transport messages remain visible, and
refused writes retain the review without announcing a committed-write warning.
An accepted write remains accepted after a failed refresh; the cached source
and raw read error stay visible with the actual current-target Retry. Confirm
and Cancel keep their existing keyboard and pending-state semantics.

The opt-in copy reflects best-effort SCM write-back rather than promising every
app receives a commit. Successful commits may deploy apps together; missing
source connections and write-back failures can skip apps. An accepted metadata
change does not prove edge rollout, authentication readiness or complete app
migration. Existing unsupported provider/capability limits remain explicit in
central-auth-envoy.md; this card does not add a cluster/app enumeration or new
paging promise.

Focused proof: 237 current-source checks pass across all-eight real Apollo
HttpLink ClusterSettingsClient outcomes, exact class/id/sync payloads and read
variables, simultaneous refusal/read-failure modes, keyboard confirmation,
Cancel/no-write, initial failed/null source retry, source withdrawal, cached
target changes, a delayed accepted prior-target HTTP reply, late pure dialog
completion, existing permission withdrawal, locale changes, ICU/rich-message
parity, recoverable-error-free hydration and portable central/ingress stories.
Radix Select legitimately populates its selected text/options after hydration;
checks preserve request-local heading/copy and the literal hydrated identifier
without requiring identical post-effect HTML. Existing central OIDC journeys
also pass; its production view/hook are unchanged. All pre-existing message
trees exactly match signed base d3528e61 outside the owned ingressClass subtree.

This bounded selector is complete; #2145 remains open. AWS/Cognito ingress-auth
configuration and source pickers, auth-user lists, cluster header/tab/connection,
lifecycle/bootstrap settings and their feedback remain separate boundaries.

## People privacy accuracy follow-up (#2220)

The connected People review now uses the actual nullable server anonymous flag
and exact current user GUID/name. A confirmed anonymous account offers repeated
privacy cleanup; deactivated or anonymous-looking identities with false/unknown
flags retain the ordinary review. Real Member lifecycle stays deactivated.
All eight genuine locale messages explain conditional first cleanup, bounded
attributed history/cache/contact reduction, retained role bindings and structural
records, unsupported structured/unattributed data and external/archive copies.

Focused current-schema HttpLink journeys cover ordinary and repeat actions,
exact mutation inputs, refusal and transport failures, accepted cleanup with
failed refresh, truthful self-logout navigation recovery, permission/row/filter
withdrawal, cached same-target read failure, late completion and fresh
acknowledgement after target changes. ICU argument parity, locale changes,
closed-server-review hydration and six portable dialog stories cover presentation.
This is the bounded #2220 UI contract; unrelated #2145 cluster/AWS/Cognito and
other untranslated settings boundaries remain open.

## Connected Cognito ingress-auth settings

IngressAuthView/useIngressAuth and the exact ingress-auth navigation label use
54 genuine `clusterSettings.ingressAuth` messages in all eight locales. Known
cloud product names, ARN/client/domain identifiers, raw diagnostics, exact
mutation inputs and the existing parent `access.update` fence remain literal
and unchanged. This card edits public Cognito metadata; the already-localized
central OIDC write-only secret fields remain untouched.

A saved config is labelled configured, never proof of active traffic protection.
The two-stage write still updates `albAuthConfig` before reconciling cluster
Ingresses. Refused saves retain drafts and cause no read or reconciliation;
accepted saves survive later refresh/reconciliation failure with separate
saved-but-unconfirmed recovery feedback. Reconciliation reports actual counts,
partial failures and skipped Ingresses without claiming gateway readiness.
Disabled configuration never claims all apps are public.

Exact cluster/provider/region/class/source context changes reset drafts and
picker state. Source withdrawal/restoration cannot revive a pending old review;
a late accepted old-cluster reply cannot reconcile or close a new-cluster edit.
Same-target cached read failure retains the current draft. A newly selected pool
without a known domain clears the previous domain instead of inheriting it.
Pool/client errors show raw diagnostics and retry their actual queries. Returned
choices and empty lists do not prove complete cloud inventory or permissions;
the existing backend may return an empty list for unavailable driver enumeration.
No paging/full-coverage contract, cloud operation or new admission gate is added.

Focused proof covers schema-validated actual ClusterSettingsClient HttpLink
writes/reads in every locale, accepted/rejected/transport/two-stage failures,
refused-write retry preserving exact draft inputs, keyboard submission, partial
and skipped reports, disable outcomes, source read retry/withdrawal, failed
picker retry, actual pool selection with missing domain, stale target/source
replies, current permission fencing, locale changes, ICU parity and hydration.
Twelve portable ingress-auth stories and the prior central OIDC regression suite
also pass. All pre-existing catalogue values outside the additive owned subtree
and the prior central/ingress-class source remain preserved.

This bounded card is complete; #2145 remains open. Auth-user lists, remaining
cluster header/tab/lifecycle/bootstrap copy and other untranslated settings
are separate boundaries. Unsupported provider-specific paths remain unsupported.

## Connected cluster sign-in user inventory

AuthUsersView/useAuthUsers and its exact cluster-settings navigation label now
use 73 genuine `clusterSettings.authUsers` messages in all eight locales. The
known provider status presentation is mapped explicitly; future metadata, IDs,
usernames, emails, groups, query/filter/sort values and server diagnostics remain
literal. Existing CLUSTER_USERS and shared-cluster operator admission stays
server-authoritative and the actual parent permission fence is unchanged.

The same seven mutation documents/payloads remain in use. Refusals preserve
private drafts and run no refresh; accepted provider requests stay accepted
through failed reads. Invitation/reset requests do not claim delivery. A created
group is reported separately from a later refused membership update. Failed or
unknown first reads show unavailable/retry rather than healthy empty inventory;
failed cached reads retain observations. Confirmed withdrawal, changed clusters,
observed same-username replacements and source ABA transitions invalidate old
reviews and callbacks. Late accepted password replies cannot clear a newer
cluster's credential draft, while a language change alone preserves the draft.

Focused proof uses the actual ClusterSettingsClient and schema-validated Apollo
HttpLink requests for every write in every locale, literal input envelopes,
refusal/transport/fallback messages, accepted-refresh failures and real source
retry, plus cached recovery, partial group operations, permission withdrawal,
source leases, locale changes, ICU parity, SSR hydration and portable stories.
The three focused files pass 440 checks; TypeScript, scoped ESLint and formatting
pass on the frozen source. No query, SDL, backend, central/ingress sibling or other catalogue subtree changes.

Inventory remains bounded by the provider's first returned subset (60 users,
60 groups, first 60 memberships/user for Cognito); local pages/search/filters do
not enumerate the rest. Client leases cannot prove immutable configured pool or
provider-user identity. The separate deduplicated backend follow-up is
[APP #2225](https://github.com/calliopeai/astrolift-app/issues/2225). This card is a
bounded #2145 leaf, not closure of the remaining cluster header/tab/bootstrap,
lifecycle or other untranslated settings surfaces.

## Shared cluster header and navigation

ClusterHeader/ClusterTabs now use 32 genuine `clusters.chrome` messages in all
eight locales, including their tab/aria labels, known lifecycle descriptions,
inactive/default missing copy and breadcrumb switcher presentation. The pure
localizedClusterCrumbs factory is opt-in: the existing static clusterCrumbs,
list declarations and route/active-tab predicates retain their contracts.
Names, slugs, regions, provider identities, unknown lifecycle values and future
navigation labels stay literal. Caller-supplied emptyTitle, primaryAction and
menu nodes retain their meaning and authority; this header supplies none.

Focused proof covers actual ClusterSettingsClient/GetCluster Apollo HttpLink
journeys in every locale for all four known lifecycle values. Existing failed
read versus confirmed-null titles, cached failed-read recovery/current Retry,
changed observed targets and permission-fenced cards remain intact. Keyboard
breadcrumb/tab navigation preserves exact hrefs and active marks. ICU parity,
locale changes, SSR hydration, literal future metadata and 15 portable header/
tab stories accompany the existing cluster list-variable/declaration tests.
There are 120 passing focused checks, with TypeScript, scoped ESLint and source
formatting green. Existing locale trees outside this additive subtree match the
signed base exactly; query/schema, read/mutation hooks and sibling cards are
unchanged. No live requests or new permissions are introduced.

This bounded chrome leaf does not close #2145. Remaining connected English
includes lifecycle action/confirmation/outcome copy in list/detail/settings and
their mutation hooks, bootstrap recipe and history/release tables, cluster status
bodies and ClusterTabFrame recovery copy. Their existing mutation acceptance,
refresh-failure, target-review and recipe source-state risks require separate
bounded work; no semantic changes to those paths are claimed here.

## Bounded list/detail cluster-unregistration flow

ClustersList/ClusterDetail unregister entries, confirmations and their exact
useClusterActions outcome path use nine genuine `clusters.unregister` messages
in all eight locales. Literal GUID/slug inputs, provider diagnostics, permissions,
query variables and sibling Bring/Refresh actions retain their contracts.
Unregistration wording describes registration retirement and existing app/model
cleanup guards, without promising infrastructure destruction or record erasure.

The unconditional awaited refresh previously could turn an accepted unregister
into a displayed write failure or refresh even a refused write. This flow now
refreshes only accepted envelopes through the shared committed-write feedback
helper; original refusal/transport messages and reviews remain intact. Accepted
writes survive refresh/navigation exceptions. Visible-row/permission/list-context
review leases discard old selections and callbacks after replacement, withdrawal
or source ABA; late completion cannot close a newer review or navigate another
detail route. Locale changes alone preserve the reviewed target. No server
immutable incarnation/version precondition is introduced.

Proof: 223 checks pass across actual ClustersClient and ClusterDetailClient
schema-validated HttpLink journeys in all eight locales, refusal/transport/fallback
and retry, accepted-read/navigation failures, exact target inputs/active query
variables, Cancel/no read or write, permission/identity withdrawal, stale callbacks,
late completion, locale/ICU and SSR hydration. Thirty portable list/detail stories
include connected refusal plays; nine existing cluster declaration/list-variable
checks remain green. TypeScript, scoped ESLint and formatting pass. Source schema,
typed documents, frozen chrome and catalogue values outside the additive owned
subtree are preserved.

This closes only the unregister action flow. Bring/Refresh/force-preflight,
settings decommissioning, general list/detail labels and read-state gaps,
bootstrap recipe/history, status bodies and other untranslated cluster settings
remain separate #2145 boundaries. No backend/cloud actions or full-suite proof
are claimed for this leaf.

## Reviewed auth-user source and subject preconditions (#2225)

The subsequent #2225 contract adds authoritative nullable provider pool/source
proof and immutable provider-user subjects to the connected inventory and
optional expected-target fields to all seven existing writes. The web hook sends
literal observed proofs, includes source revision in its source lease, and
refuses missing proof. The view retains observations while disabling source/user
operations with unknown proof; unknown identity is not reported as a failed read.
Existing presentation keys cover these states in all eight locales; no locale
catalogue or sibling settings changes are needed.

The original 440-check presentation proof above describes its frozen source.
The extended schema-validated HttpLink/portable/card proof includes unknown
source/user identity and exact expected-source/subject envelopes in every locale.
Provider-side conditional CAS remains unsupported; see
[reviewed-target operator contract](../operators/cluster-auth-user-preconditions.md).

## Connected app settings archive and restore

`ArchiveAppView`, `useArchiveApp` and the actual `ArchiveAppCard` use 14 genuine
`apps.settings.archiveFlow` messages in every locale. The existing translated
confirmation title retains the literal app name, and accepted feedback identifies
the exact requested slug. Relative archival time uses the configured locale,
request clock and timezone. Copy describes saved replica counts and deployment
suppression, without claiming an observed live rollout or infrastructure removal.

The exact existing `archiveApp`/`restoreApp` inputs remain `{ appSlug }`; the
existing `app.update`/optimistic-loading fence and backend checks are unchanged.
Only accepted envelopes collect the existing active GetApp refresh with its
original variables. Reads run after acceptance/navigation handling, and the
shared refresh helper warns without changing a committed result into a write
failure. Refusals keep the review and raw diagnostic, with no refresh/navigation.
An accepted archive remains accepted when navigation throws. Mutation replies
are not normalized into a different review before acceptance is handled.

The actual card passes its observed app GUID/version/archive facts. Target/source
and permission withdrawal, replacement and ABA transitions invalidate stored
callbacks and reviews; an old accepted response cannot navigate or close a
newer review. A language change preserves an otherwise unchanged review. Same-
target cached observations survive failed reads as before. These are local
observation safeguards, not server immutable identity/version preconditions:
the current backend still resolves the slug and remains authoritative.

Focused proof on the ec1abcd6-based leaf: 168 checks pass in four files, including
130 actual-card/schema-validated Apollo HttpLink/locale/context/review checks,
10 portable stories and 28 existing operational/recovery regressions. Both
operations cover every locale and actual payloads, raw refusal/transport/fallback,
accepted-refresh/navigation failures, keyboard retry, current permission and
source withdrawal, stale callbacks, late replies, ICU argument parity and
recoverable-error-free hydration. TypeScript, scoped ESLint (zero warnings),
source/catalogue formatting and diff checks pass. All existing catalogue trees
are exact outside the new subtree. No broad suite, backend/schema/cloud change,
installation or live request was performed.

This completes this card within original item 13, not the whole item. Retention,
environment overrides, managed-service administration, agent-mode resync and
remaining operational-hook feedback are separate connected translation gaps.

## Connected app-frame delete feedback

The remaining `useAppFrame` delete success/fallback strings use four additive
`apps.frame.deleteFeedback` keys in all eight locales. Slugs, IDs and provider,
transport, refresh and navigation diagnostics remain literal. The existing
`SOFT_DELETE_APP` document, `{ input: { id } }` payload, `app.delete` visibility
gate, confirmation and `/apps` navigation request remain intact.

A refused or failed mutation keeps confirmation open, retains its diagnostic and
runs no Apps-list refresh or navigation. An `ok: true` response reports the
accepted soft deletion, then reads the same `LIST_APPS` query over the network.
Refresh failure produces a localized warning and still requests navigation;
synchronous navigation failure produces its own localized warning. Both retain
the accepted write and close confirmation, without issuing a second delete. The
warning description keeps any actual diagnostic. The pending confirmation
remains disabled until the write and its follow-up settle.

The six focused files pass 149 checks: 72 real schema-validated HttpLink/frame
cases across all eight locales, eight ICU/key-contract cases, 23 portable
AppFrame story checks and 46 existing detail/chrome/polling regressions. Portable
components use the actual project decorators and RTL-owned cleanup. Catalogue
comparison to `ec1abcd6` confirms exactly four new leaves per locale and no
changes outside this subtree. TypeScript, scoped ESLint and formatting pass.

This proves the displayed API acceptance and local follow-up boundaries. It
does not establish downstream workload teardown, completed browser navigation,
provider deletion or new lifecycle authority. Archive/restore, other app-frame
feedback, API/backend/schema and other catalogue subtrees are outside this leaf;
#2145 remains open for its remaining surfaces. No production or cloud call ran.

## Connected app settings retention policies

`RetentionPolicyView`, its owned `useRetentionPolicy` and the actual settings
`RetentionPolicyCard` use 14 genuine `apps.settings.retentionFlow` message leaves
in all eight locales. Signal labels, default guidance, accessible picker names,
day units and accepted/refused/refresh feedback are translated. ICU plural
arguments and branches remain equivalent while permitting each language's word
order. Recorded positive periods outside the card's fixed presets stay visible;
this adds no new preset. Known human signal labels use an exact mapping; unknown
technical signal identifiers remain literal in the presentation helper.

The existing technical signals, presets `[7,14,30,60,90,180,365]`, default of 30,
GetApp query and `{ appSlug, signal, retentionDays }` mutation input are unchanged.
The existing `app.update` and optimistic-loading fence is preserved, with backend
owner/permission/validation checks remaining authoritative. No backend, schema,
generated contract or new authority/pagination behavior is introduced. Copy
explains that a saved retention policy is not proof that old data was deleted.

Refused, missing and failed writes cause no refresh or accepted-write warning;
raw diagnostics remain literal and the recorded selected period stays visible.
Accepted writes refresh the original active GetApp variables through the shared
safe feedback helper, preserving acceptance if the read fails. A failed same-
target cached read retains the observed controls. A missing/unavailable initial
app read does not render a healthy default-retention card. The outer settings
read/retry presentation remains unchanged and outside this leaf.

The actual card passes observed GUID/version/policies. Changed source facts or
permissions close old pickers and invalidate stored callbacks, including source
ABA; language changes alone preserve an open selector. Pending state is tracked
by app observation and operation identity, so a late app A completion cannot
clear app B's current save, and one signal's successful refresh cannot clear
another pending signal on the same app. These are local observed-source
safeguards, not server immutable-incarnation or compare-and-swap guarantees.

Focused proof on the ec1abcd6-based source: 275 checks pass in two dedicated
files (267 actual-card/schema-validated Apollo HttpLink/locale/context checks and
8 portable stories). Every locale and all four signals cover exact inputs,
accepted/refused/missing/transport/failed-read outcomes, refusal-plus-read-failure
without a read, keyboard retry, source/permission withdrawal and replacement,
stored callbacks, concurrent pending ownership, known/unknown literal metadata,
ICU parity, genuine translations and recoverable-error-free hydration.
TypeScript, scoped ESLint (zero warnings), source/catalogue formatting and diff
checks pass. Existing eight catalogue trees are exact outside the owned subtree.
No broad suite, installation, live call, cloud or publication was performed.

This completes this retention card within original item 13, not the whole item.
The independently frozen Archive/restore leaf is unchanged. Environment
settings/overrides, managed-service administration, agent-mode resync and other
operational-hook feedback remain separate connected translation boundaries.
