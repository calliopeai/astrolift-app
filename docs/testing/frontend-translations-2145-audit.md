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
Members item remains incomplete until connected mutation feedback and the
anonymization/grant dialog bodies are translated and verified.

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
