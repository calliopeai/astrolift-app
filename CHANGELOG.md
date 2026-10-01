# Changelog

## Unreleased

- Localize agent source-resync settings and feedback in all eight locales. Keep
  accepted registration results through failed reads, preserve raw refusals without
  refetching, and invalidate stale observed-source callbacks. Registration does not
  claim a started run, completed redeployment or provider health (#2145).

- Keep Event Grid Basic child ownership under the shared Azure verifier and
  expose its unchanged binding envelope directly to the cross-driver contract
  checks. Actual observed labels, exact ARM identity and strict source checks
  remain required (#2032, #2098).

- Azure Event Hubs rejects conflicting source-ID ownership aliases and checks
  bounded subscription/resource-group inherited locks before effects, including
  initial namespace creation. Requires ancestor `Microsoft.Authorization/locks/read`;
  no independent lock is deleted and other Azure families remain unchanged
  (Refs #2032, #2098).

- Localize list/detail cluster-unregistration reviews and feedback in all eight
  locales. Preserve refused reviews without refreshing, retain accepted writes
  through failed reads/navigation, and invalidate stale visible-target reviews.
  Registration retirement does not claim infrastructure teardown or record
  erasure (#2145).

- Localize shared cluster headers, tabs and breadcrumb presentation in all eight
  locales. Preserve technical identities, unknown metadata, navigation targets
  and caller-owned source titles/actions; lifecycle and bootstrap operations
  remain separate translation boundaries (#2145).

- Azure topic/default-subscription aliases use full immutable service UUID names
  and save exact ARM placement/child coordinates. Typed SDK 10 writes, current
  parent/child ownership and complete bounded inventory gate supported effects
  and sender bindings. Unknown legacy provenance stays refused; denied cleanup
  never becomes success. Only actual topic capacity/TTL updates are editable;
  retention and backup limits remain explicit (#2032, #2098).

- Bind reviewed cluster sign-in user writes to an observed provider pool/source
  revision and immutable provider user subject. Recheck locked source and current
  authority before SDK effects, reject recreated usernames, preserve optional
  legacy caller semantics, and make unknown review proof read-only in the web UI.
  Persist existing tracking markers on partial cluster/provider source saves;
  Cognito get-to-write races and partial multi-call effects remain explicit (#2225).

- Localize the connected app archive/restore settings card in all eight locales.
  Preserve refused reviews without reads or navigation, retain accepted writes
  through refresh/navigation failure, and invalidate stale observed app reviews.
  Explain saved replica counts without promising a live rollout (#2145).

- Azure native/Kafka Event Hubs uses complete immutable UUID names for new
  targets and exact saved ARM placement for lifecycle and bindings. Current
  source/platform/child identities, bounded complete inventories and operator
  lock refusal gate effects; unknown observations never become cleanup success.
  Actual SDK12 enum/wire values and NoPolling preserve pending versus observed
  readiness. Legacy ambiguous handles refuse unchanged; retained-data/Capture
  limits and exact hub-scoped Sender/Receiver grants remain explicit (#2032, #2098).

- Localize app-frame delete feedback in all eight locales. Preserve accepted
  soft deletion through failed Apps refresh or navigation; refused writes retain
  their confirmation and exact diagnostics (#2145, bounded item 5 flow).

- Localize the connected app retention-policy settings card and feedback in all
  eight locales. Preserve signal IDs/day presets and raw refusals, refresh only
  accepted writes, and retain acceptance after read failure. Bind selectors and
  pending indicators to the observed app/source without promising data deletion
  or backend version preconditions (#2145).

- Localize the connected environment override card in all eight locales. Keep
  refused drafts and accepted settings through failed reads, show real read retry,
  and discard drafts after observed environment, app, source or permission changes
  without changing current-target API writes (#2145, bounded item 13 flow).

- Localize secret-proposal read errors, known operation/status/decision labels
  and fallback summaries in all eight locales. Preserve supplied summaries,
  technical unknowns, diagnostics, masked diff content and action authority
  without changing proposal mutations (#2145, bounded item 14 presentation).

- Azure Event Grid Basic retains the full immutable service UUID in new topic
  and child names, and preserves complete recorded ARM targets. Current topic,
  child and inherited-lock observations gate supported lifecycle and bindings;
  unknown/foreign sources and retained data refuse even under force. SDK
  NoPolling keeps accepted work pending until actual observations confirm it.
  Historical ambiguous targets require separate recovery; Standard namespaces
  and the writable-label/incarnation residual remain outside this repair
  (#2032, #2098).

- Retire the legacy direct-claims session relay with an explicit HTTP 410 and
  backend login URL. Posted identity claims and the legacy static relay key no
  longer mint or replace sessions. Existing verified OAuth login/callback,
  CLI device approval and normal session/bearer admission remain supported;
  legacy relay clients must migrate to backend login (#2224).

- Localize the connected cluster sign-in user inventory and all seven existing
  operations in eight locales. Preserve refused drafts, distinguish accepted
  requests from confirmed delivery, and retain accepted writes through failed
  reads. Retry actual source reads and invalidate stale cluster/user reviews;
  provider inventory remains a bounded returned subset (#2145, #2225).

- Localize the connected Cognito ingress-auth card and pool/client source states
  in all eight locales. Distinguish saved configuration from reported Ingress
  reconciliation and actual traffic protection. Keep refused drafts and exact
  inputs, retain committed writes through later failures, and discard old-cluster
  or withdrawn-source drafts (#2145).

- Azure `queue/servicebus` preserves its permanent unsupported in-place update
  contract without SDK reads or writes, including foreign/unavailable targets.
  Supported lifecycle paths retain their actual ownership checks (#2032, #2098).

- Azure `queue/servicebus` uses the full immutable managed-service UUID for new
  names and preserves recorded queue paths. Actual source/platform metadata
  and ARM target checks now gate lifecycle, readiness and workload bindings;
  refused or inaccessible cleanup never becomes success from diagnostic text.
  The Azure extra requires Service Bus SDK 10.0 for its real ARM metadata
  field. Retained queues and unsupported snapshots remain truthful; sibling
  topic/subscription drivers are outside this bounded repair (#2032, #2098).

- Privacy review uses the server-confirmed anonymous-state flag and exact current
  user identity. All eight locales distinguish first cleanup, repeated attributed
  history cleanup and retained/unsupported records. Changed targets or withdrawn
  sources require fresh acknowledgement; accepted cleanup stays accepted after
  refresh or sign-out navigation failure (#2220).

- Account anonymization reduces supported, attributable historical PII while
  retaining structural audit facts, foreign subjects and stored role bindings.
  Database guards admit only the exact reduction for an inactive anonymous
  account; failures roll back the account transition. Repeat cleanup preserves
  the anonymous identity, and a nullable user status supports accurate review.
  IdP callbacks preserve subject/issuer ownership and refuse inactive accounts
  before restoring caches or issuing sessions. See
  `docs/operators/account-anonymization.md` for the source inventory and deliberate
  exclusions (#2220).

- GCP Pub/Sub queues use full immutable service-ID names for new resources and
  preserve exact recorded paths. Current source/default-child ownership,
  bounded inventory and finite SDK budgets gate operations. Retained-data
  cleanup never seeks/discards messages without explicit deletion; failed child
  deletion and force flags cannot fake success (#2032, #2098, bounded scope).

- Pub/Sub cleanup preserves failed/unknown ownership outcomes across permission
  errors, incomplete subscription inventory and denied child operations. Error
  text and caller-controlled names cannot become successful deletion; concrete
  typed missing-resource outcomes remain idempotent (#2098, bounded scope).

- Localize ingress-class choices, gate guidance, opt-in deployment review and
  outcomes in all eight locales. Preserve exact update inputs and raw refusals;
  accepted changes survive failed reads, rejected changes trigger no refresh,
  and cluster/source changes discard old reviews (#2145).

- New GCP Pub/Sub topic names retain the full persisted service UUID and reserve
  room for distinct declared subscription suffixes. Recorded topics and child
  mappings stay unchanged; colliding legacy child declarations, invalid IDs
  and unavailable ownership proof refuse before mutation (#2032, bounded scope).

- Translate the connected central OIDC authentication form and feedback in all
  eight locales. Preserve write-only secret omission and literal provider
  metadata, retain committed updates through failed refreshes, and clear
  previous-cluster/source drafts without changing mutation authority (#2145).

- Translate connected People mutation/CSV feedback and the anonymization dialog
  in all eight locales. Describe actual account/profile and membership effects;
  keep historical-audit and stored-binding limitations explicit (#2220).
  Preserve accepted writes across failed reads, skip refused-write refreshes,
  await clipboard success, honor required self-logout, and bind irreversible
  acknowledgement to the selected account (#2145).

- Translate People list headings, filters, views, group counts, export actions
  and invitation confirmations in all eight locales. Last-active ages use the
  request clock and selected locale; identities, role slugs, server query tokens,
  raw refusals and confirmation requirements remain intact (#2145).

- Translate deployment Metrics and pending human reviews in all eight locales,
  preserving model/app identities, query/filter values and raw read diagnostics.
  Failed Metrics reads offer retry and retain the last observed snapshot;
  unavailable success rates stay distinct from measured zero. Pending review ages
  use the request clock, and approver names use locale-aware list formatting
  (#2145).

- Shared header breadcrumb landmarks and switcher accessibility names use all
  eight locales, preserving caller labels, destinations and keyboard focus
  through hydration and locale changes (#2145).
- Localize the cluster heartbeat-agent key/deploy/install and clipboard outcomes
  in all eight locales. Preserve accepted mutations and one-time keys through
  failed refreshes, distinguish failed cluster reads from confirmed not-found,
  and retry the real current-target read without changing permission decisions
  (#2145).

- Localize Team reach and connected access/removal presentation in all eight
  locales. Preserve source identities and server refusals, retain accepted
  removals through failed refreshes, and block old-target confirmations while
  the current target is unavailable (#2145).

- Translate connected Team detail/member navigation, role assignment and source
  recovery in all eight locales. Preserve literal identities and request-timezone
  dates; accepted assignments remain accepted if their read refresh fails, and
  refused writes retain selection without refreshing (#2145).

- Localize the connected legacy role-grant sheet and feedback in all eight locales,
  preserving literal role/scope identities and original backend refusals. Distinguish
  unknown, failed and unsupported target sources; accepted grants refresh the exact
  active page once, while cancel and rejected writes trigger no refresh (#2145).

- Refresh Team list/picker reads only after an accepted create/edit/delete reply.
  Rejected writes retain their original diagnostic and drafts without triggering
  an unrelated failed-read warning (#2145).

- Translate connected API key list/create/reveal/revoke/detail and reviewed stock
  scope guidance in all eight locales. Preserve literal identifiers, future server
  metadata, authority decisions and exact mutation inputs; distinguish metadata
  read failures from missing keys, and report clipboard success only after the
  browser completes the copy. Rejected writes do not trigger list refreshes (#2145).

- Translate connected Team list/create/edit/delete presentation and feedback in
  all eight locales, preserving scope decisions, identifiers and mutation inputs.
  Keep accepted team writes successful when their list refresh fails; refused
  or unavailable writes retain drafts and their original diagnostic (#2145).

- Localize connected Home layout settings and Home-specific persistence feedback
  in all eight locales. Copy reflects account synchronization and offline browser
  fallback; original refusal diagnostics and exact preference patches remain intact
  (#2145).
- Translate Home layouts, panel states, approvals and observed metric presentation
  in all eight locales while preserving access decisions, resource identities,
  original diagnostics and cursor callbacks. Activity ages and day headings use
  the configured locale, request clock and time zone (#2145).
- New S3/SQS/DynamoDB physical names retain the complete persisted service UUID,
  avoiding joined/truncated tenant-slug collisions. Reprovision keeps validated
  recorded names and data; missing identities, invalid targets and foreign or
  unmarked pre-existing resources refuse before mutation. Other driver families
  and excluded explicit/SES identities remain separate work (#2032, #2098).

- SQS and DynamoDB updates, teardown and readiness verify live source identity
  and exact recorded target before actions. DynamoDB snapshots also verify the
  source and returned backup identity. Unknown tags/metadata and permission
  errors cannot masquerade as absent resources; force flags cannot bypass
  ownership. Queue snapshots remain unsupported (#2098).

- Explicit provider ownership refusal or unavailable ownership proof never
  becomes successful cleanup because a resource name/diagnostic contains a
  not-found marker. Genuine missing-resource teardown still converges (#2098).

- SQS workload `manage` keeps message operations and queue purging while omitting
  tag mutation and unrestricted queue-policy editing. Ownership tags and
  permission administration no longer follow from the app workload grant;
  existing identities need policy reconciliation (#2098).

- Verify live AWS source ownership before S3/SQS/DynamoDB bindings and S3 mount
  updates or retained/forced teardown. Refuse unknown or replaced incarnations,
  incomplete DynamoDB tag pages and missing/mismatched resource identities;
  never fabricate an `UNKNOWN` table ARN grant (#2098).

- Spanner Graph dedicated containers bind immutable organization ownership,
  preserve recorded physical names and require persisted contender plus complete
  cloud database-set proof before capacity changes or empty-container deletion.
  Operator-shared containers refuse tenant resize/deletion. Server-owned cleanup
  and observed-placement records distinguish confirmed cleanup from intent and
  retain original placement across failed reprovision (#2100). See
  `backend/providers/docs/spanner_ownership.md` for legacy provenance limits.

- Refuse all normalized platform ownership labels in Pub/Sub and PSC tenant
  config. Verify Pub/Sub source and child ownership before writes or binding,
  preserve ownership labels during updates and retain recorded topic names.
  Audit AWS/Azure custom-tag namespace isolation (#2098).
- Add disposable CNPG operator acceptance through durable consumer Secrets,
  real host/URI authentication, role/database privilege checks, stable partial
  retries and physical finalizer-driven cleanup, with credential-free provenance
  and explicit runtime/integration boundaries (Refs #2092).

- Restrict generated CNPG preview logins to their own database over TLS, ahead
  of broad operator authentication defaults, and explicitly remove elevated
  role attributes and inherited memberships (Refs #2092).

- Reobserve immutable CNPG parent ownership and its current complete spec on
  bounded HTTP 409 role-reconciliation retries. Skip an already matching role,
  preserve other preview roles and reuse issued credentials (Refs #2092).

- Materialize CNPG preview bindings from durable slice identities with independent
  retained credentials and namespace-qualified URLs. Recheck current owner/source
  ancestry before effects, refuse legacy or foreign slice adoption, preserve
  unrelated binding values, and report pending slice cleanup honestly (Refs #2092).

- Qualify generated native managed-service binding hosts with their recorded
  namespace, including connection URI aliases. CNPG URLs retain rotating
  operator credentials through `fqdn-uri`; legacy handles without a namespace
  refuse refresh instead of guessing a tenant destination (Refs #2092).

- Shared model by-ID reads and locks include current organization constraints
  directly, including exact cluster-scoped provider locks. Install-shared
  placement remains available only within a current tenant context (#2213).

- Add server-side Hugging Face search with bounded filter-bound pagination,
  immutable revision resolution, source timestamps and honest unavailable or
  unknown metadata (#2214).
- Deploy organization-owned shared vLLM models directly to clusters without
  application/project placeholders, using explicit certified CPU/GPU runtimes,
  immutable revisions and complete resource admission (#2213).
- Reconcile named app-environment subscriptions with independent credentials,
  exact namespace/app/environment network selectors and startup authorization
  snapshots. Recreate rollouts and observed consumer readiness distinguish
  accepted requests from applied access and confirmed revocation (#2213).
- Recheck current actor, bearer scopes, grants, placement identities and versions
  after locks and upstream reads. Consumer/ancestor retirement requires confirmed
  revocation; cluster and organization retirement also require shared deployment
  cleanup. Durable database defaults preserve old inserts during rolling upgrades.
- Make shared deployments the primary Models catalogue with server search,
  filters, Mine and paging. Preserve existing app/project/cloud endpoints and
  deployment routes; missing legacy hardware facts remain unknown (#2215).
- Connect reviewed deploy, subscribe, revoke, resource-update and retained-data
  deprovisioning actions to the actual typed APIs in all eight locales. Refusals
  retain drafts; accepted requests stay pending through read failures without
  automatic replay or inferred readiness (#2215).
- Show exact-service observed model metrics and tenant-only density with units,
  windows, source, timestamps, limits and explicit availability. Separate desired
  and applied requests from measured use; preserve applied-snapshot provenance
  across failed updates. Unverified shared hardware capacity and GPU/VRAM
  attribution remain unknown or unsupported (#2214).
- Add explicit bounded shared-model tests through the real cluster relay with
  current owner/version/provider/handle admission and truthful failures. Browser
  callers cannot supply endpoint URLs or credentials (#2213).
- Webhook pause confirmation actions wrap inside the dialog for longer localized
  labels, including the French layout found by browser CI.

- Grant access uses genuine copy in all eight locales for selection, expiry,
  review, exact preview counts, partial outcomes and retry feedback. Selected
  identities, permissions, server diagnostics and expiry payloads remain intact.
  A failed access-list refresh warns after successful writes without repeating
  those grants; failed or empty previews/results cannot report success (#2145).

- Preserve custom or future access scope labels literally instead of requesting
  missing translation keys.
- Model prompt relay admission, dispatch and result transitions atomically retain
  the matching cluster slot. Concurrent heartbeats dispatch at most once and
  replayed results preserve the first outcome without releasing a newer job.
  Cache failures return generic mutation/result failures while authenticated
  heartbeats remain healthy; admitted jobs may still finish after polling fails.
- Install discovery advertises `apps.dependency_context` and
  `providers.reference_read` support while keeping actual dependency/provider
  data off the curated public schema. Capability flags grant no permissions
  (#2208).

- Read-only app operators can resolve an exact live environment to redacted
  cluster/provider identities and persisted heartbeat/domain/certificate
  observations with `app.read`. Mutation gates remain unchanged. Expected
  cluster/provider GUIDs refuse replacement or reassignment; authenticated
  singular provider references remain resolvable past the catalog's 100-row cap
  (#2208).

- Confirmation dialogs retain handled false outcomes and entered reasons for
  retry without duplicating action diagnostics. Workload restart confirmations
  propagate the actual outcome; existing void-success callbacks still close.
- Object and queue snapshot refreshes keep their dialogs open, disable duplicate
  refreshes while pending and retain the selected service’s last snapshot with
  visible read diagnostics on failure. Initial read errors no longer look empty.
- First enabling workload HPA preserves an existing Deployment's observed
  replica count through a conditional, non-forcing ownership handover. Shared
  cloud apply drivers refuse stale/replaced targets or unconfirmed handovers;
  dry runs remain non-mutating.

- The browser workload-controls regression checks the actual three-replica web
  and zero-replica worker fixtures independently, keeping unobserved readiness
  unknown rather than inventing healthy pod counts (#2145).
- Shared policy sentences and editors use all eight locales with grammar-aware
  clauses, condition validation and JSON-shape feedback. DENY/ALLOW semantics,
  technical values, raw custom conditions and serialized payloads are preserved
  (#2145).

- Shared policy condition guidance and simulation summaries use all eight
  locales, preserving unknown-condition denials, recorded-history limits,
  server catalog metadata, diagnostics, notes and actual holder targets (#2145).

- Shared access diagnostics and comparisons use all eight locales for verdicts,
  reasoning labels and states. Actual resolver diagnostics, technical binding
  targets, permission IDs and comparison partitions remain intact (#2145).

- Shared role summaries and permission/scope pickers translate presentation
  defaults in all eight locales, retaining literal role metadata, permission
  slugs, target IDs, server diagnostics and disabled selection checks (#2145).

- Translate shared principal search, empty/error states and selection controls in all eight locales, preserving provider diagnostics and selected identities (#2145).

- Translate grant-source phrases, principal removal labels and Home’s app ownership note in all eight locales while preserving identifiers, edit links and callbacks (#2145).
- Shared settings navigation, danger-zone notices, default save/cancel actions
  and read-only permission sentences use all eight locales. Actual permission
  IDs, caller labels, server diagnostics and draft/retry behavior are preserved
- Successful agent secret value/reference and bundle/key writes remain
  committed when their list refresh fails. All eight locales explain that the
  view needs refreshing and the write should not be repeated (#2145).

- Agent secret value and bundle editors translate presentation and feedback in
  all eight locales, identify the explicitly selected environment recipe and
  retain provider identifiers and server diagnostics. Failed status/catalog
  reads show retryable errors; attachment reads finish before empty states or
  attach controls appear (#2145).

- Shared feeds translate defaults and calendar-day headings in all eight
  locales. Grouping follows the configured timezone across daylight-saving
  transitions; paging callbacks, caller overrides and server errors stay intact
  (#2145).

- The explicit agent environment-spec picker translates access boundaries,
  shared-recipe notices, search and pagination in all eight locales (#2145).
- Shared list and table controls, empty/error/loading states, selection and live
  row notices use all eight locales. Counts honor the selected locale; caller
  labels, server diagnostics and actual paging/filter/sort values are preserved
  (#2145).

- Shared numbered and cursor pagination translates controls and count sentences
  in all eight locales, retaining page/cursor callbacks and unknown totals.
- Shared confirmation dialogs use all eight locales for default actions,
  pending labels and validation feedback, preserving server errors and retry
  behavior (#2145).
- Connect the model playground to actual authorized vLLM prompt relays with
  server-paged endpoint selection, advisory readiness, truthful failures and
  bounded sequential batch cancellation. Replace demo history/starred data with
  validated browser-local records scoped to the active organization and user;
  keep unrelated topology/observability showcases and forms parked (#2148).
- Managed-service summary, connection metadata and test-email/object/queue dialogs
  use all eight locales, including byte quantities, cache age and queue counts.
  Actual service identifiers, metadata, reference shims and rejected-email drafts
  remain intact; read failures retain their server diagnostics (#2145).

- Deployment controls and settings destination cards use all eight locales.
  Section modification captions localize both the sentence and relative time;
  image-tag retries, replica staging and actual destination routes remain intact (#2145).

- Source resync, ingress, webhook pause and one-shot job controls use all eight
  locales, including audit attribution and operational help. Actual job and
  environment selections and rejected-pause reasons remain intact (#2145).

- Recovery, deregistration resource previews and grace-period cancellation
  warnings use all eight locales. Exact app-name/slug confirmation guards,
  resource identifiers, workflow IDs and rejected-action retry remain intact (#2145).

- Deployment action menus, reasons, target warnings and outcomes use all eight
  locales. Missing mutation responses now reject confirmations; pending requests
  block resubmission and failures retain the entered reason for retry (#2145).

- App deployment history, views, filters and comparison sheets use all eight
  locales. Compare still requires two actual deployments in chronological order;
  source links, manifest paths and values remain intact (#2145).

- App access views, grant/source removal copy, home-team labels and outcomes
  use all eight locales. Removal confirmations retain the actual grant or team
  share across failure and retry; home-team shares remain protected (#2145).

- Complete missing app settings group and identity messages in every locale,
  including the editor’s save action. Blank names remain refused, failed saves
  retain user edits and version conflicts use the localized shared notice (#2145).

- Global preview lists, detail panels, states and teardown warnings use all
  eight locales. Resource numbers and USD costs respect the locale; actual PR
  targets, hostname patterns and driver cost caveats remain intact (#2145).

- Environment list views, filters, pause confirmations, outcomes and detail
  panels use all eight locales. Ownership/kind filter values, target links and
  actual settings remain unchanged; failed pauses require a confirmed retry (#2145).

- Deploy strategy editor copy and notices are translated in all eight locales.
  Branch help retains its actual identifier, rejected saves keep edits for retry,
  and missing-message version conflicts use the localized shared fallback (#2145).

- Shared version-conflict fallback notices and their Refresh action use the
  selected locale; actual server-provided messages remain unchanged (#2145).

- Translate actual model-playground readiness, prompt limits, cancellation, and
  browser-local history notices across all eight UI locales (#2148, #2145).

- Shared version-conflict feedback can supply translated fallback text and
  refresh actions without replacing server diagnostics or replaying mutations.

- Translate the bounded real-model playground's prompts, local-session notices,
  readiness and failure states in all eight locales; preserve ICU parameters.

- Agent secrets require an explicit visible environment-spec choice, verify its
  current identity before opening editors, and target that recipe instead of
  guessing from the agent slug. Recipe-wide edits are labeled; switching agents
  or organizations clears selection and revealed values (#2148).
- Scaling feedback passes actual ICU values, and API key/project operation dates
  honor the selected locale and timezone. Project operation states and fallback
  scale errors have translations in all supported locales (#2145).
- App registry columns, kinds, views, filters, pins, bulk outcomes and secret-push
  dialogs use all eight locales. Translated labels preserve real query values,
  and an unsuccessful secret push retains selection and form values (#2145).

- The app deploy-activity strip localizes labels, numeric hints, empty copy and
  deployment status tooltips while retaining real deployment links (#2145).

- App detail deploy/delete confirmations, config-drift notices and URL health
  hints use all eight locales. Translated confirmations preserve actual image,
  environment and app identifiers and require acceptance before writes (#2145).

- App frame, section/tab labels and shared app loading/not-found messages are
  translated in all eight locales. Locale changes preserve actual route targets,
  active tabs and retry callbacks (#2145).

- Complete the 73 missing app overview translations in each non-English locale,
  including deployment states, health, ownership, CI and service summaries. The
  probe clock respects the selected locale and configured time zone (#2145).

- App activity filters, search and count/empty states use all eight locales.
  Reprovision notices use real translated fallback keys for unknown states;
  confirmations retain failure-and-retry behavior (#2145).

- Audit feed labels, filters, decision badges, retention and export copy use all
  eight locales. Translated filter labels preserve the original server values;
  counts use locale-aware ICU plurals (#2145).

- Platform metrics translate fleet states, range controls, metric labels and
  unavailable-provider guidance in all eight locales while preserving actual
  cluster/provider identifiers and links (#2145).

- Feature controls, confirmation dialogs and mutation notices use the selected
  language in all eight supported locales, including rich environment-variable
  hints. Failed confirmations remain open for retry (#2145; remaining domains
  are still being translated).
- Custom-domain DNS, certificate, routing and ingress controls use all eight
  locales. Certificate dates follow the locale; DNS records, PEM values and
  the existing external-domain cookie limitation are preserved (#2145).

- App and agent configuration forms, manifest previews, local validation and
  save feedback use all eight locales. Generated TOML and technical diagnostic
  paths retain their original identifiers (#2145).

- App preview dialogs, countdowns, statuses and spend warnings use all eight
  locales. Currency follows the selected locale; unpriced and approximate
  estimates retain their qualifications (#2145).

- App secret controls, scopes, history and mutation feedback use all eight
  locales. Dates and expiry counts follow the locale; secret values, scope
  identifiers and server-provided errors stay unchanged (#2145).

- Edge-access rule descriptions, editor controls, validation and save feedback
  use all eight locales. Preview counts use locale number/plural formatting;
  group names, email identities and access policy stay unchanged (#2145).

- App supply-chain security copy, vulnerability badges and numeric threshold
  labels now use all eight locales. Security-event timestamps follow the locale,
  and findings labels refresh when the language changes (#2145).

- Deploy-token controls, rotation metadata states and exact grace durations now
  use all eight dashboard locales. Expiry and last-use dates follow the selected
  locale; technical token scopes and one-time secret values are unchanged (#2145).
- HPA-managed Deployments release replica ownership so an image redeploy does not
  overwrite a live autoscaler count. Fixed-size deployments and preview clamps
  keep explicit replicas; document separate workload, CPU-node and GPU policies.
- Add selected-endpoint model prompt readiness and harden the existing real vLLM
  prompt relay against retired/incoherent owners and inactive clusters before
  heartbeat, configuration, rate, or job reads. Readiness is advisory and exposes
  only invocation limits; reported agent versions do not establish relay support
  (#2148).

- Deregistration loads its authorized resource preview before displaying the
  count badge, refreshes on confirmation and treats unavailable/refused reads
  as unknown. Confirmation waits for a usable preview (#2148).
- Deploy-token rotation confirmation reads the actual validated configuration
  through an app-update scoped metadata query. Loading, refused and missing
  metadata block confirmation; each opening rereads, ignores stale replies and
  shows the exact duration and read-time snapshot semantics (#2148).

- Policy condition JSON passes through the existing validating parser without an
  object-only type or double assertion; Storybook fixtures use actual arrays (#2148).

- Pipeline detail and secrets pages show the existing definition's actual name
  with explicit loading/error/unavailable states. Agent repository pickers link
  directly to the canonical Source providers section (#2148).

- Environment override clears, secret bundle attachments/deletions and secret
  reference saves prevent pending repeats and show the affected action as busy.
  Reference saves use trimmed variable identities; closing or changing a secret
  dialog cancels reveal timers and ignores late reveal responses (#2148).

- Introduction and local setup describe the current Astrolift control plane and
  actual development commands. The dashboard links canonical project history
  when versioned release notes are unavailable, treats unknown note categories
  neutrally, and documents the image's real unbuffered-logging default (#2148).

- Shared page chrome uses a common context outside app routes. Remove unreachable
  active-provider/session toast callbacks and unused approval/download props;
  approval history uses semantic theme tokens (#2148).
- Agent run controls read stored replicas, loop concurrency and scaling schedules
  before saving. Logs and shell share live pod/container targeting, including
  container deep links; ingress Apply reflects every busy phase. App and agent
  wizards share repository/progress UI and refresh their active-org project
  destination before registering (#2148).

- Audit managed runtime reservation and revocation, and redact provider failures
  during managed Stop without losing retry or retained-storage recovery (#1971).

- Provision explicit managed IDE boxes with a supervised server/browser, retained
  CSI storage and projected, renewable runtime credentials (#1971). Failed starts
  and Stop revoke ownership; private resource creation and deletion honor Kubernetes
  ownership preconditions across all four providers. Managed Move remains unadvertised.

- Add internal certification and box-scoped validation for managed IDE runtime
  ownership (#1971). Exact live Job, Pod and persistent claim identities are
  checked before binding a runtime incarnation. Managed provisioning and Move
  remain unavailable until their separate lifecycle and transfer integration lands.

- Merge the agent startup-diagnostic and durable-backlog migration branches so
  a combined control-plane upgrade has one migration leaf. Both additive
  migrations remain applicable from either previously deployed branch (#1972, #2190).

- Agent task backlog snapshots survive pod termination and are readable through
  `agentTaskBacklog(orgId, taskId)` with the same scoped authority as task events
  (#1972). Negotiated runner callbacks persist ordered, bounded snapshots outside
  the event feed; an explicit empty list clears prior progress without changing
  event types or cursors.
- Team and project create sheets generate backend-compatible slugs and preserve
  a requested visible team. Workflow configuration waits for and scopes reads
  to the active organization, create access stays unknown while loading, and
  agent-only attention links target actual agent pages. Metrics links Temporal
  only when `NEXT_PUBLIC_TEMPORAL_UI_URL` is configured (#2148).

- Operational forms stay blocked during secret rotation and require a command
  container. Terminal agent-run aliases stop polling, uppercase running tasks
  accept overseer input, help copy timers are cleaned up, and webhook writes
  block only their own subscription while preventing duplicate actions (#2148).

- Public install discovery has a dedicated, rate-limited GraphQL transport with
  only the curated server handshake. Mobile clients can inspect an install before
  login while the main API retains its authentication requirement (#2185).

- Manifest conflict resolution labels the unsaved local-draft choice as “Keep
  mine”; saving remains a separate action. Workload controls show pod readiness
  as unknown instead of treating desired replicas as healthy pods (#2148).

- Managed AWS CI previews use the backend workflow and the private ECR registry
  region. Unconfigured deploy-only workflows omit AWS authentication; invalid
  owner/registry coordinates refuse before driver or repository writes. Reference
  workflows retain the actual deployment branch (#2148).

- Managed clusters offer an explicit full-preflight refresh. Cluster status waits
  for observed connectivity before reporting an absent agent, and metrics charts
  use distinct SVG gradients when multiple clusters render together (#2148).

- Web workload restart and scale controls use object `viewerCan` decisions,
  retain denial reasons and refresh authority after structured failures. Settings
  name the actual primary-environment target once; workload version preconditions
  protect stale actions (#1867). Other objects, navigation and CLI acceptance
  remain pending.

- Workloads expose advisory `viewerCan.restart` and `viewerCan.scale` decisions
  using actual owner, bearer, grant/share and primary-environment policy checks,
  batched across a page (#1867). Mutations still recheck; web/CLI consumption
  and the remaining allowed-action acceptance are pending. See
  [workload action permissions](docs/operators/viewer-actions.md).

- Token creation accepts the documented zero-day value for no expiry (#2148).

- Config editor section links recognize the manifest codec's managed-service
  headers. Workload links navigate within the app; identity-provider labels show
  ellipses only for truncated identifiers and report activation dates only when
  observed. Correct organization paths, missing-form copy and prerequisite
  documentation spacing (#2148).

- Approval queue counts and select-all follow currently visible rows after
  polling. Invalid invitation expiries remain unavailable, alert mute submissions
  enforce whole hours from 1 through 168 and block pending repeats, manifest
  diagnostics retain zero coordinates, and blank image tags show the deployment
  identifier (#2148).

- Production route checks follow rendered navigation for every active route in
  the generated dictionary (#2171), with separate alias, parked-route and role
  checks. Cold workflow-run pages wait for workflow context before rendering.
  See [route navigation checks](docs/testing/route-navigation.md).

- Identity lists export every authorized matching member, invitation and role
  binding as CSV, preserving filters and stable sorting without the old 5,000-row
  cutoff or blank member roles. Invitation activity uses only unambiguous live
  current-org membership and audit evidence; subject aliases and role/policy
  sort declarations complete the existing paging contract (#2153). See
  [identity list exports](docs/operators/identity-list-exports.md).

- Persisted deployment logs retain actual owner deny policies and current bearer
  ceilings after app/environment teardown or cluster retirement. Failed history
  diagnostics preserve the original provider failure; completion writes still
  fail the activity when durable storage is unavailable. Durable database defaults
  keep old log writers compatible during migration-first rollout (#2176).

- Add explicit, reviewed agent secret-owner maintenance tools (#2102): private
  metadata-only plans, full-payload copies before atomic ref changes, operator and
  paused-writer checks, and resumable application without source deletion. Runtime
  owner enforcement remains a separate release after migration; existing refs
  continue to resolve. See [the maintenance sequence](docs/operators/agent-secret-owners.md).

- Topology traffic exposes measured Istio request/error rates per directed
  intra-app edge over a bounded window (#2177). Actual app ownership, bearer
  ceilings and environment policies apply before HTTP or cache reads. Empty,
  unavailable and unconfigured sources remain explicit; no synthetic rates
  fill gaps. See [topology traffic](docs/operators/topology-traffic.md).

- Dashboard polling and refreshes keep the active filtered page current (#2143).
  The app header owns the shared deploy poll across tabs; task and security
  reads filter before limits. App alert counts cover every visible firing and
  deployment charts walk their complete fourteen-day window. Direct event,
  alert and rule reads keep old links independent of recent-list caps, with
  owner, bearer and environment-policy filters preserved. See
  [dashboard query freshness](docs/operators/frontend-query-freshness.md).
- Agent environment specs have an org-scoped paged list and detail home under
  Agents (#2178), with server search/filter/sort/counts and live owner/bearer
  visibility. Optional detail `orgId` validates the explicit tenant; the client
  separates identical slugs and refresh snapshots by organization.
- Human-gate email delivery records a failed, error-severity event when no
  recipient resolves, the mail transport refuses the message or returns zero
  deliveries (#1823). The gate remains reviewable; accepted transport delivery
  is distinct from inbox receipt. See [sender setup](docs/operators/human-gate-email.md).
- App workload identity policies union grants across coherent live environments
  on the target cluster (#2093), so a preview deploy preserves production access.
  Shared policy and namespace trust reconcile serially; Azure outcomes retain
  each consumer's owner. Empty AWS unions remove the platform inline policy.
  See [workload identity](docs/operators/preview-names-and-workload-identity.md).

- Preview names remain distinct when manual branch `pr-N` and PR #N collide
  (#2095). Both paths serialize name and namespace allocation per app, including
  duplicate concurrent requests. Explicit occupied names return validation on
  `environmentName`. See [preview naming](docs/operators/preview-names-and-workload-identity.md).

- App Pods preloads import their scoped query-variable builder from a shared
  server-safe module, so the production Metrics route no longer calls a
  client export from a server component (#2171).

- `/pipelines/new` opens a real form using the existing organization-owned
  creation contract (#2171). Failed creates retain their draft; navigation
  failure after commit offers the created pipeline link without a duplicate
  create. Fleet, approvals, pipelines, operations, logs and traces have rail
  links, and Fleet links its map. Route generation distinguishes real pages
  from computed compatibility aliases and ignores quoted redirect examples.

- Pipelines expose scoped write-only secret APIs backed by encrypted
  `OrgSecret` storage (#2171). Secret metadata reads and writes resolve live actual owners
  and retain bearer organization/team ceilings. Runtime dispatch reads the
  same pipeline namespace, refuses stale owners and does not fall back to
  bare or sibling names. The Secrets route opens its section; failed refreshes
  after committed writes remain distinct from rejected writes. See
  [pipeline secret access](docs/operators/pipeline-secrets-access.md).

- App cards provide separate app, pin and failed-deployment keyboard actions
  without nested links or buttons (#2144). Email bounce and complaint details
  expose their disclosure state and named details region. The one-time webhook
  secret reveal uses the shared modal with a title, description, focus trap,
  Escape dismissal and focus return after rotation.

- Core legacy APIs enforce active account, platform-operator and bearer
  ceilings without removing GraphQL declarations (#2110). Permission analysis
  checks explicit organization management; legacy self-deletion keeps
  elevation and the last-owner floor. Live directory/upload ownership filters
  and declared export/support route gates close all 42 remaining guardrail
  gaps. Unavailable legacy actions refuse before resolving supplied IDs.
- Operations gates resolve app, project, team or organization owners and retain
  bearer ceilings (#2107). Events, alerts and webhook collections filter before
  aggregation, paging and counts; alert subscriptions require app read access.
  Bulk actions authorize every app and affected operation before acting, and
  secret attachment also authorizes its source bundle. Audit trails, retention,
  notification settings and Zentinelle management require organization authority.
- Agent Observe logs use an additive structured page API with timestamp,
  level, stream and stable cursors (#2175). Each page checks current task
  ownership, bearer ceilings and policies; frozen dispatch placement supplies
  the source. Temporary bounded pod-log snapshots support loading earlier
  lines and report their live-only limits. The legacy string-list query is
  unchanged. See [agent task log pages](docs/operators/agent-task-log-pages.md).

- Rollback, redeploy, workload restart and scale accept optional top-level
  `ifMatchVersion` preconditions (#2162). Clients read deployment or workload
  versions from the target row; stale requests return structured version
  details before writes or external calls. Actions lock and recheck the live
  target and environment; rollback selects an older superseded revision only
  within the current running deployment's environment. Existing callers may
  omit the argument. See [mobile quick-action preconditions](docs/operators/mobile-quick-action-preconditions.md).

- Lifecycle gates authorize coherent live app owners and preserve bearer
  team/share ceilings before collection counts and side effects (#2104).
  Builder requests check the stored owner and destination; invalid clusters
  cannot reach provider calls. Lifecycle events recheck persisted operation
  facts, current identity and policies per event, and close broker queues on
  cancellation. Historical deployment links and public approval-token
  contracts remain supported. See [lifecycle access scopes](docs/operators/lifecycle-access-scopes.md).

- Managed-resource gates resolve live app, project, team or organization owners
  and preserve bearer ceilings across GraphQL and all ten project-resource MCP
  tools (#2106). Collections filter before limits and counts; attachments check
  destination ownership and persisted environment facts before any writes.
  Missing or stale targets require explicit organization authority. Private
  resource provider targets are checked through the app environment; deleted
  or foreign clusters are refused before provider access, including for an
  organization operator.

- Workflow manifest previews accept `workflow.read` at any granted scope
  without writing (#2114). Flow and new TOML imports require organization
  `workflow.create`, independent of selected team/project headers. Compatible
  replacements authorize their existing project owner; a changed shape still
  creates an organization version and requires organization create permission.
  Existing update permission, source-managed refusal and bearer organization/
  team ceilings remain enforced. Replacement rechecks the destination under
  definition/stage locks before writing; all three guardrail gaps are removed.

- Non-inheriting organization bindings authorize organization-owned collection
  rows without revealing descendant teams, projects or apps (#2164). Deleted
  roles stop granting authority through user, group and group-mapping bindings.

- Identity administration gates resolve explicit organization or live target
  owners (#2103). Team/project and binding collections filter grants and bearer
  ceilings before counts; scoped binding managers retain their real target
  authority and grant ceilings. Selected headers cannot authorize owner misses,
  and project writes verify coherent live team ancestry. Organization bootstrap
  and personal operations retain their existing contracts. See
  [identity access scopes](docs/operators/identity-access-scopes.md).

- Pipeline creation requires `app.update` at the active organization (#2115).
  The existing creation input has no app association, so team/project/app
  grants and team-bound bearer credentials cannot authorize this organization
  object through selected headers. Organization grants retain the existing
  validation and response contract.
- Form management, response reads and private submission require their
  declared permissions at the organization's explicit owner scope (#2112).
  Selected team/project grants and team-bound bearer tokens cannot reach
  organization forms through an owner's broader role. The submission stream
  requires `form.read`, silently refuses unauthorized callers, filters the
  exact live form and organization, and closes polling on cancellation.
  Published public submissions retain their existing anonymous exception.
  See [form access scopes](docs/operators/form-access-scopes.md).

- Scoped operation checks resolve owner factories under trusted facts and isolate
  permission-scope caches per target (#2164). Log subscriptions admit the verified
  environment and cluster, release operation context before yielding events, and
  close their provider streams safely across tasks.

- SCM connection and OAuth/GitHub App routes require their explicit
  organization permission; per-app SSH keys and CI actions authorize their
  owning app (#2109). SSH collections filter rows before pagination and counts.
  SCM uses the registry's live, coherent app ancestry, so a stale project or
  mismatched project team cannot widen a bearer credential's app ownership.
  Team-bound bearers cannot borrow an org owner's or operator's wider scope.
  Named repo/file reads constrain connection IDs to the active organization,
  preserve personal credential ownership and refuse unavailable connections
  before provider access. Installation callbacks refuse stale state from another
  organization before exchange/write.
  All 27 tracked surfaces are covered without guardrail exemptions.

- Billing queries and quota increase requests require `billing.read` at the
  active organization, regardless of the selected team/project (#2113).
  Team/project grants and team-bound bearer tokens cannot reach organization
  costs, budgets or quotas, even when a token owner has an organization role.
  Organization-bound billing automation retains the `admin` token ceiling;
  authorized requests keep the existing response and notification behavior.
  See [billing access scopes](docs/operators/billing-access-scopes.md).

- Observability gates resolve live app/service ownership with explicit organization
  fallback and permission-specific bearer team/share ceilings (#2111). Selected
  team headers cannot authorize missing, stale or sibling targets. Historical
  logs keep their separate permission. Internal metric catalogs no longer become
  accidental GraphQL root fields; existing data-query contracts are unchanged.
  See [observability access scopes](docs/operators/observability-access-scopes.md).
- Registry app and workload gates resolve live tenant-owned targets and take
  explicit organization scope on missing, ambiguous or stale ownership (#2105).
  Collections filter rows before pagination; team-scoped bearer credentials
  retain their owner/share ceiling despite an organization-wide user role.
  Registration and transfer destinations are checked separately, and source
  manifest scans require organization authority.

- Agent task and box startup diagnostics expose scoped pod scheduling reasons.
  Pending Jobs stay provisioning until ready; timeout failures preserve the
  last startup reason before cleanup, and recovery clears stale warnings (#2190).

- CLI and IDE sign-in credentials can discover and attach to agent boxes when
  account permissions allow it. The existing `mcp:dispatch` scope now includes
  `agent_box.attach`; read-only tokens and organization/RBAC boundaries remain
  enforced (#2188).
- App cards provide separate app, pin and failed-deployment keyboard actions
  without nested links or buttons (#2144). Email bounce and complaint details
  expose their disclosure state and named details region. The one-time webhook
  secret reveal uses the shared modal with a title, description, focus trap,
  Escape dismissal and focus return after rotation.

- Frontend forms retain edits across query refreshes and failed retries (#2147).
  Successful tool and skill saves keep their accepted values until fresh data
  arrives, without clearing edits made while the save was pending. Reopened
  strategy and source connection dialogs start with current values; workload
  changes select a valid container, and stale agent discovery responses are
  ignored. Bundle names keep generating their slug until it is edited, and
  bundle and resource dialogs keep separate cluster selections. Browser storage,
  shared playground sessions and cron countdowns initialize after hydration.

- Frontend action failures show a toast or an inline form reason, while rejected
  confirmation actions keep their dialog and selection (#2146). Successful
  dispatches and token creation retain their result when a follow-up refresh
  fails; bulk deployment actions refresh the list even after total failure.
  Agent config and device pairing report copy success only after the clipboard
  write completes, and offer manual-copy guidance when it fails.
- Frontend query failures show an error with Retry inside the affected frame
  instead of empty lists, missing objects, disabled modules, static driver facts,
  or zero Operations metrics (#2142). Model deployment waits for target and GPU
  capability reads. Event rate distinguishes loading, failure and a successful
  zero-event window. Failed background refreshes preserve loaded workflows,
  agent runs, live VNC sessions and skill rows.

- Backend startup exits when schema migration fails, before dependent
  bootstrap commands or the HTTP server (#2187). Successful migrations keep
  the existing production/development server behavior, and other startup
  commands remain best-effort. The operator upgrade procedure requires a
  verified migration task before the paired web/worker rollout.

- The deployment detail Redeploy action sends the selected deployment ID required
  by the API, and reports server or network failures in a toast (#2173).

- AWS deployments and rollbacks protect ECR images with immutable per-environment,
  per-deployment tags before writing Secrets or applying workloads, and pin
  container references to the protected digest. Tags include the full digest to
  prevent collisions in adopted mutable repositories. FaaS and private static
  builder images are protected before service, identity or Job writes. Each
  rollout stage merges into the current snapshot and reuses its first protected
  digest, keeping existing pins even when a source tag moves. Successful rollout
  history retains ten deployments per workload; live, failed and in-flight deployments remain protected. Repositories
  enroll in the preview-first age/count cleanup job on creation or adoption
  (calliopeai/astrolift-opscode#69).
- Outbound webhooks retry transient failures with Temporal's replay-safe jitter
  instead of failing the workflow sandbox (#2165). Permanent HTTP errors and
  unsubscribe responses still stop immediately; retries retain signed delivery
  records and replay without sending new requests.
- Workflow reconciliation settles exact executions with purged Temporal history
  once, logs at INFO and stops looking them up (#2166). Running mirrors display
  History expired with an unknown outcome, existing final results stay intact,
  and owned-task cleanup continues without querying missing history. Apply
  additive migration `astrolift_operations.0027` before worker rollout.

- Upload confirmation by URL and ID now requires the uploader and active
  membership in the upload's organization; a platform operator can manage
  another uploader's file only within the selected organization (#2174).
  Deleted files cannot be confirmed again. Generic upload bearer writes need
  `admin` and the same organization GUID across the legacy upload and token
  identities. `/app/metrics/` requires an active platform operator, with
  `admin` for bearer requests; the Sentry and OpenTelemetry debug routes are
  removed. Existing anonymous Prometheus scrapes must use an operator bearer.
  Upload initiation retries a caller-supplied UUID only for the same uploader
  and organization; another owner's, another organization's or a deleted
  upload cannot be reassigned through that UUID (#2110, partial).
- Request-ID and trace context cleanup now consumes each token once, so a
  handled view exception preserves its original HTTP status and request-ID
  header when Django subsequently runs response middleware (#2174).
- Cluster, managed-domain and provider-configuration routes require their
  explicit organization owner scope (#2108). Team/project grants and
  team-bound bearer tokens, including operator/admin tokens, cannot authorize
  organization resources through a selected team. Shared domain workflow writes
  check both the cluster and domain; shared bootstrap history requires the
  platform operator. All 48 tracked routes have real RoleBinding coverage and
  no surface-guardrail exemptions remain for this issue.

- Agent surfaces are isolated per team and project, and a guardrail keeps
  every surface declaring its permission and scope (#1866).
  `AgentEnvironmentSpec` and `AgentBox` gain nullable `team` and `project`
  owners; a null spec owner means org-shared, explicitly. Migration 0039
  gives each spec the project of the one agent that shares its slug, and a
  spec-only box its spec's owner; a slug two apps' agents share stays
  org-shared. Spec reads narrow to the org-shared specs and the ones a grant
  covers (new `teamId`/`projectId` on `AstroliftAgentEnvironmentSpec`), and
  spec writes check at the owner, org-shared ones at the org
  (`createAgentEnvironmentSpec` takes an optional `teamId`/`projectId`).
  Dispatch, `ensureAgentBox` and workflow agent stages run an agent only with
  a spec its own app may use, so a team can no longer run with another
  team's secret packet. Boxes are read, destroyed, ensured and attached at
  their own scope (recorded project or team, else the agent's app, else the
  org), and a live box is never handed to an ensure from another scope.
  Skills, tool defs, briefs, org skill repos, secret values, secret bindings
  and org secret bundles check at the explicit org scope instead of the
  selected team. The exec relay checks `app.exec_pod` on the app and
  `agent_box.attach` on the box, under the bearer's scope ceiling; the app
  log subscriptions check `app.read_logs` on the app. The Dispatch Service
  log and meter ingest routes, which took any caller's writes into any run,
  now require the dispatcher key and stay in its org. MCP agent tools
  declare their scope; `astrolift_sync_agent_repo` and
  `astrolift_import_agent_spec` check at the named project, and an import
  no longer moves another project's agent. Manifest sync and agent import
  stamp a new spec's owner and refuse to rewrite another scope's spec.
  Rewriting an org-shared spec during registration requires an explicit
  org-level spec-update grant. Team-scoped credentials cannot create,
  update or delete shared specs even when their user has an org role;
  shared reads and owned-spec registration remain available.
  Registering against a team-shared recipe also requires its owning team's
  spec-update grant and respects the credential's team ceiling; a project
  grant alone cannot rewrite the recipe used by the team's other projects.
  Team-scoped credentials create specs only inside their own team and
  cannot launch org-level image-only or shared-spec boxes. They can still
  launch their own agent with a shared recipe or a box on an owned spec.
  `core/tests/test_surface_guardrail_1866.py` walks the served GraphQL
  schemas, the URL conf, the WebSocket routes and the MCP registry, and
  fails on any route without a declared scope that is not on its allowlist;
  the remaining gaps outside the agent surfaces are tracked per area there.
  Still open (#2102): agent secret refs are confined per org only, so a spec
  writer can bind another team's secret location.
- Deployment, run and secret permission gates now carry the resolved environment,
  cluster region and durable approval total (#2164). Environment-specific policies
  leave other environments usable; app-wide writes check all affected environments,
  and exact workflow controls cannot borrow approvals from another execution.
- Scoped policies now reduce collection rows and capabilities, including a grant
  whose only app is denied; an allowed parent cannot restore a denied child (#2164).
  Policy writes reject malformed conditions and selector shapes before saving.
- Operation collections apply each row's environment, frozen dispatch region and
  recorded approvals before pagination, totals, deployment comparisons and health
  metrics (#2164). Secret and service lists filter their actual environments;
  app-wide secret proposals check all affected environments. An approved run
  cannot lend its votes to another run's read.
- Deployment approvals now record distinct voter identities under a row lock
  (#2164). Repeated requests are idempotent, and a legacy counter or anonymous
  bearer link cannot supply human identities to an ABAC approval requirement.
  Bulk approvals and rejections apply each deployment's actual app-scope policy.
- SCIM Groups now supports org-confined provisioning, reads, atomic membership
  PATCH, replacement and deletion with the existing SCIM credential (#2164).
  Removal immediately drops group-derived roles without changing direct grants;
  stale SSO claims cannot restore a removed, deleted or rekeyed group identifier.

- A managed service restores only from a snapshot Astrolift retained for its
  own app, and no longer runs as an identity its config chose (#2087).
  `restore.snapshot_id` and `restore.source_handle` came from the manifest
  and every driver's `restore` read what they named with the platform's
  credentials: another org's backup, revision or export on a shared cluster.
  The platform now refuses, before any driver runs, a pair that does not
  match a `last_retained_snapshot` a data-preserving teardown recorded on a
  service of the same organization, app (or project), kind and cluster, and
  restores that record's own point in time. Config that copied data around
  restore is refused: Firestore `clone_source_database`, the Neptune,
  DocumentDB, Redshift, MemoryDB and ElastiCache Serverless snapshot keys,
  Neptune replication and global-cluster joins, FSx Lustre import and export
  paths, Amazon MQ replica brokers, REST API `cloneFrom`, and Redshift
  `owner_account`. Workflows' `allow_cross_project_snapshot` and
  `allow_unowned_snapshot` are gone. Service accounts, user-assigned
  identities and roles a config names must be listed by the operator in the
  cluster's `provider_config`, and an empty list refuses every one:
  `workflows_`, `eventarc_`, `pubsub_` and `api_gateway_allowed_service_accounts`,
  `cloud_operations_allowed_writer_identities`, `bigquery_allowed_connections`,
  `eventgrid_`, `eventgrid_namespace_`, `eventhubs_`, `cosmos_api_` and
  `managed_redis_allowed_identity_resource_ids`, `mssql_allowed_option_groups`
  and, in a shared `knative_namespace`, `knative_allowed_service_accounts`.
  Lambda `grants` and VPC endpoint `iam_grants`, which wrote config-chosen IAM
  statements onto the role tenant code runs as, are refused, and a Lambda
  update resets its execution role to basic execution. Redshift `iam_roles`,
  API Gateway integration and authorizer credentials and Firehose processor
  roles must sit under the org's IAM role path like other roles. Upgrading:
  a restore from a snapshot taken outside Astrolift, or from another app or
  cluster, stops working, and configs that name one of these identities fail
  their next provision or update until an operator lists it.
- Status colours no longer follow the accent (#2126, spec 35 §A.1). A
  healthy dot and a running deployment were coloured with the selectable
  accent, so with the copper, ice, periwinkle or amber accent they turned that
  colour. `StatusDot`, `DeploymentStatusPill` and `RunStatusBadge` now share
  one tone map (`lib/status-tones.ts`) built only from the status tokens: ok is
  the success lime whatever the accent, and in-flight states stop pulsing
  under reduced motion.
- One tab bar for every entity detail page (#2126, spec 35 §A.5). App, agent,
  cluster and workflow detail drew four copies of the same link strip; they
  now render `components/DetailPageTabs.tsx`, one row of tabs, and keep only
  their own route models. Each row is `min-w-0`, so a long label scrolls
  inside the strip instead of widening the page. Cluster tabs gain the edge
  fade the others had, and the app's sub-tab row matches the others' height.
  The app detail keeps its BROCS pillar bar locally until it moves to one row
  of tabs by function (spec 44).
- Bedrock bindings on an inference profile grant what the call needs (#2137).
  A `model_id` such as `us.anthropic.claude-sonnet-4-6` (or a profile ARN) is
  resolved with `GetInferenceProfile` when the binding is built, and the
  workload's role is granted invoke on the profile and on every foundation
  model it routes to. Before, it got `foundation-model/us.anthropic...`, which
  is not a resource, so the call was always denied. A direct foundation model
  keeps its one grant, and a profile that cannot be resolved is refused
  rather than granted on nothing. Redeploy (or reconcile) a consuming app to
  pick up the new grants.
- Per-app access on the Envoy edge (#2132). An app lists the identity
  provider groups and users (by email) that may enter, from its Security tab,
  `astro app access`, or `[ingress.access]` in `astrolift.toml` (which then
  manages it, and the UI only shows it). The edge enforces it before the app
  sees a request: the one shared SecurityPolicy gains per-host `authorization`
  rules on the ID token's groups and email claims, so single sign-on stays,
  and a user turned away gets a page naming the app instead of Envoy's bare
  403, while an app's own 403 passes through. An app with no rule is open to
  every signed-in user, as before. Saving shows how many users could enter
  and who would lose access. Proven on Envoy Gateway 1.9.1 before building.
- Manage who can sign in to the apps behind central auth, from cluster
  settings and `astro auth-users` (#2131). A new `identity_users` driver
  capability, Amazon Cognito first (Entra ID, Identity Platform and Keycloak
  are on the availability matrix as planned), lists, creates, disables,
  enables, deletes and resets users, sets passwords, and manages groups on
  the install's own pool. Passwords are write-only: no response carries one,
  the audit log masks them, and a provider error that echoes one is
  scrubbed. A shared cluster's pool holds every org's logins, so reading it
  is the platform operator's. New permission `cluster.users` (Cluster Owner)
  and token scope `manage:auth-users`; `app.access` and `write:app-access`
  land for #2132.
- A deploy says why it failed or what it is waiting on (#2123). Each row of an
  app's Deployments tab shows `statusReason`: the abort reason or the first
  line of the build or manifest error for a failed deploy, and for a pending
  one whether it waits on approval, is queued behind a named deploy, or has
  not started with nothing ahead of it (which past ten minutes reads as a
  missing worker). The opened row shows the full build output and the
  manifest resync error beside the log.
- Central auth and the ingress class on the cluster settings page (#2119).
  A Central auth card sets `oidcAuthConfig` with secrets write-only (set or
  not set badges; a blank field keeps the stored value, and the server now
  carries forward any secret, or `proxy_extra_args`, an update omits). An
  Ingress class card warns before a change that would leave apps with no
  gate and asks whether to also commit the gate to every app's manifest,
  which redeploys them all. The recipe card keeps what the recipe installed
  checked and no longer pre-checks a controller the probe found running
  outside it, such as an ALB controller or external-dns installed by hand.
  An additive edge install now records the recipe's whole set, so it never
  makes the next operator run read the rest of the recipe as foreign.
- Token scopes for the cluster surface and a picker that explains itself
  (#2120). `write:clusters` (update a cluster's settings) and
  `manage:clusters` (run its recipe, refresh, reconcile) replace the admin
  token a script used to need; registering and removing a cluster stay
  admin-only. The picker reads `astroliftApiTokenScopeCatalog`, derived from
  enforcement: scopes grouped by surface, what each unlocks, presets, the
  scopes your roles can never exercise shown disabled with the reason, and a
  plain warning on `admin`. Each token lists its effective permissions
  (scopes narrowed by the owner's roles), and a long-lived admin token is
  flagged. A device login with client kind `cli-operator` asks for the CLI's
  scopes plus the cluster pair.
- The Envoy edge comes up from the installer with no hand steps (#2130).
  `register_tenant_cluster` accepts the OIDC client secret without an
  oauth2-proxy cookie secret, and a cluster on class `envoy` with a complete
  config gets `envoy-gateway` installed on start, or when an update moves it
  there. That install is additive: it deletes no other release, and it never
  runs for a cluster on any other class, so existing nginx and ALB installs
  are unchanged. On a cluster whose recipe offers them it also brings the
  AWS Load Balancer Controller and external-dns, but only when a live probe
  finds neither running, so a hand-installed controller never gets a twin.
  The probe now sees an external-dns in `kube-system`.
- A fresh install gets its apps zone from the installer
  (`bootstrap_managed_domain`, from `ASTROLIFT_MANAGED_DOMAIN_ZONE`,
  `_ZONE_ID` and `_CERTIFICATE_ARN`). It registers the zone once as the
  platform default for tenant apps and never touches an existing row.
- A cluster's class flip no longer redeploys every app (#2122). The flip
  used to commit the new gate to every bound app's `astrolift.toml`, and
  each commit started that app's deploy. The deploy path renders from the
  cluster, not the manifest, so each app now moves on its own next deploy;
  `updateTenantCluster(syncManifests: true)` still commits it.
- The recipe pre-checks oauth2-proxy only on an nginx-family cluster
  (#2121). On an Envoy or ALB cluster it had nothing to gate and needed a
  Secret nothing writes.
- Central auth on an Envoy Gateway edge (#2055). A cluster with
  `ingressClass: "envoy"` and a complete `oidcAuthConfig` serves every
  platform-assigned app hostname through one Gateway in `astrolift-edge`,
  with Envoy's own OIDC filter owning the single IdP callback on the auth
  host and a session scoped to its parent zone. A new app needs no Cognito
  callback, load balancer or DNS record; on EKS an ALB in front keeps TLS on
  the zone's ACM certificate. It replaces the ingress-nginx edge, which is
  past end of maintenance, and closes a leak that edge had: oauth2-proxy
  forwarded its session cookie to every app backend, where Envoy removes
  the session's HMAC, expiry and refresh cookies and keeps the token cookies
  encrypted. Identity reaches apps as `X-Auth-Request-User` and
  `X-Auth-Request-Email`, dropped at the listener when a client sends them.
  An app moves on its next deploy, and its old ALB Ingress is removed only
  once the edge Gateway is serving. `oidcAuthConfig` now accepts a
  write-only `client_secret`, read back as `client_secret_set`. See
  `docs/operators/central-auth-envoy.md`.
- GCP ownership checks no longer key on a name the tenant chooses (#2086,
  follow-up to #2074). Spanner Graph proved a database was Astrolift's with
  a schema marker that named no service, and on a shared instance the
  derived database id carried no org, so two orgs with the same app,
  environment and hint shared one database: the second provision
  reconciled it, applied its own DDL and bound `roles/spanner.databaseUser`
  on it. Private Service Connect keyed ownership on the tenant-set
  `endpoint_id`, and Cloud Operations on a bundle id built from the
  service's name alone, which also let one org's default prune delete
  another's resources. Spanner databases now carry an
  `AstroliftGraphOwner_<managed-service id>` table, which tenant DDL may not
  name; PSC addresses and forwarding rules carry
  `astrolift_io_managed_service_id`, reserved against tenant labels, with
  the create-time description (`resource=<managed-service id>`) read back
  as evidence; Cloud Operations bundle ids end in `--<digest of the
managed-service id>`. New Spanner and PSC derived names get the same
  digest. Existing resources keep their recorded names. One made before this
  change, and so without the marker, is accepted (and, on provision or
  update, stamped) only when the platform's record of its handle is
  exclusive: no other live managed service, on the same driver in the same
  GCP project, records it. Otherwise it is refused until an operator marks
  its owner. Eventarc, Managed Kafka and Cloud CDN also stop dropping a
  pre-#2074 adopted marker on re-provision, which had silently disarmed the
  `delete_adopted` teardown guard.
- Tenant service config can no longer adopt an existing resource on GCP or
  k8s_native (#2074, follow-up to #2021). The tenant-set flag #2021 removed
  from Cloud SQL SQL Server, Valkey and Firestore lived on in sixteen more
  drivers, and some went further: `reassign_existing` and
  `allow_reassignment` took a resource from another managed service or
  another org's boundary, and Cloud CDN's `adopt_existing` rewrote another
  stack's ownership marker. Removed, and refused unconditionally:
  `adopt_existing` and `reassign_existing` on API Gateway, Filestore, Cloud
  Functions, Eventarc (the bus and every child declaration) and Managed
  Kafka (cluster, schema registry, Connect); `adopt_existing` on Cloud CDN
  and Private Service Connect; `adopt_existing` and `allow_reassignment` on
  Workflows; the per-declaration `adopt` on Cloud Operations;
  `adopt_existing_instance` and `adopt_existing_database` on Spanner Graph,
  plus the install-level `spanner_adopt_existing_instance` switch, which any
  tenant `instance_id` reached because it outranks
  `spanner_shared_instance_id`; `adopt_existing` and `expected_existing_uid`
  on the k8s_native Argo Workflows, KServe, Gateway API, Knative Serving and
  Knative Eventing drivers, where knowing an object's uid proved only that
  the caller could see it; and SeaweedFS's `adopt_existing`, which asked the
  operator to bind an existing bucket in the shared cluster to the tenant's
  S3 identity and is now always rendered `adoptExisting: false`. API
  Gateway's `update` also stops trusting a platform label alone for the
  tenant-named `api_id`: with `allow_api_retarget` it could point the
  gateway at, patch and prune another managed service's API, and now the
  API must be this service's. A config that still sets one of the removed
  keys, even to `false`, is rejected rather than ignored. A pre-existing
  shared Spanner instance takes the `astrolift-managed-by=platform` label
  instead of the switch. The `delete_adopted*` teardown confirmations stay:
  a resource adopted before this change keeps its marker and still needs the
  second acknowledgement to delete.
- Rendered hostnames, not just app labels, are now unique per managed zone
  (#2012, follow-up to #1930). A multi-workload app's suffixed hostname
  (`<label>-<workload>.<zone>`) can equal another org's plain label of that
  name, which the #1930 label-only check could not see. A new
  `HostnameClaim` ledger records one row per (zone, hostname) a live
  app/workload/environment renders, with a database-level unique constraint
  on the rendered string, kept in sync at register, `setAppSubdomain`,
  every manifest apply (register, resync, agent repo scans), imported
  agents and the builder path, the same places #1930 already checks.
  `setAppSubdomain` refuses a rename that would produce a colliding
  suffixed hostname, since the app's full workload set is already known
  there; the manifest-apply paths are best-effort (a hostname another app
  already holds is left with its existing owner and logged, never stolen,
  since those paths must survive a bad or colliding manifest elsewhere in
  this codebase). Claims release on app teardown/deregister and on
  `softDeleteApp`. A data migration backfills the ledger from every
  existing app, keeping the first claimant (oldest app) on any
  pre-existing collision and logging the rest. `manage.py
report_shared_zone_hostname_collisions` is a new read-only command that
  renders every live app's public hostnames in shared zones independently
  of the ledger and lists every hostname two or more organizations render
  : the way to find what the migration left unclaimed, and to audit the
  ledger against the live renderer going forward.
- AWS SES: a tenant could adopt another org's or the platform's own sending
  identity by deriving, or typing, the same name (#2029). The driver now
  provisions through SESv2 -- `create_email_identity` with ownership tags,
  `get_email_identity` for both state and tag reads -- and refuses to reuse
  an existing identity whose tags don't match this managed service, the
  same `adoption_refusal` pattern #1961 established for the other AWS
  drivers. Provisioning also now refuses up front, before resolving a
  driver at all, when a different and still-live `ManagedService` row
  already holds the identity a spec would derive or was given explicitly,
  so a same-account collision never reaches SES.
- One organization can no longer act on a person's account past its own
  reach (#1979, #1977). Anonymizing someone else now needs them to be a
  member of the active organization, not the platform operator, not an
  active member of another organization, and holding nothing the caller
  could not grant, and it asks for step-up. Revoking a role binding, and
  renaming, trimming or deleting a custom role, are capped at what the
  caller could grant. An organization keeps its last owner unless the
  platform operator removes it. SCIM no longer attaches or switches off the
  platform operator's account. It answers 403 to a PUT that would change
  the email or name of an account another organization shares, and to
  reactivating such an account while it is switched off. The legacy
  `organizationMemberStatus` mutation switches off the whole account, so
  only the platform operator may call it.
- Workflow routes check their permission at the object's own scope (#1965).
  Cancelling, terminating or signalling a run needs `workflow.trigger` on the
  run's app, else on its definition's project, else on the organization.
  The check used to run with no target and then match only the organization,
  so a team or project grant with that team or project selected (a
  team-scoped API token, or an `X-Astrolift-Team` header) reached every run
  in the organization. The same rule now covers the exact-execution reads and
  controls, the Temporal viewer, `astroliftWorkflowRuns`, the configured
  workflow and definition routes, and the legacy `workflowInstance(s)`,
  `workflowStages` and `workflowStageExecutions` readers, which checked no
  permission at all. A definition without a project, a platform template,
  and anything that does not resolve are organization-level. Lists keep
  every row for an organization grant and narrow to the covered projects and
  apps otherwise, so team and project members see their own workflows
  without selecting anything. Only the platform operator acts fleet-wide on
  the viewer and on cancel, terminate and signal: the stock organization
  owner and admin roles hold `admin.elevate`, and through it they reached
  other organizations' runs. `workflowStageExecutions`, `workflowInstance`
  and `workflowInstances` no longer return rows that belong to no
  organization. `createWorkflow` now prefers the organization's own
  definition over a platform template with the same slug. A team-scoped
  import (`importWorkflowManifest`, `importWorkflowFlow`) can still create a
  disabled, project-less definition with a template's slug; enabling it, or
  configuring a workflow from it, needs an organization grant. Workflows
  started by an SCM push, an inbound webhook or a definition schedule now
  record the organization that owns the trigger: the webhook's (or its
  app's), else the definition's. They used to record none, so no tenant
  could reach them, and their agent stages resolved workloads and picked a
  dispatcher across every organization (#1984).
- Add an `applyStagedManifest` mutation for apps that have no source repo to
  push a staged edit through: it applies `manifest_raw_staged` straight to
  `manifest_raw` via the same parse-and-persist path `registerApp` uses.
  Previously `updateManifest` only ever wrote the staging buffer, and the
  only paths that moved a draft into `manifest_raw` (`syncManifestFromRepo`,
  `pushManifestToRepo`) required a source repo, so an app registered with
  `--manifest-raw` could never change its manifest after the first edit. A
  repo-backed app always goes through `pushManifestToRepo` for review,
  whatever its connection health: this mutation is reachable only for an
  app with no `source_repo` at all. Runs under `select_for_update()`, and
  the caller's last-known `rawManifestStagedHash` (a keyed digest bound to
  the app, never a bare hash of the text) is checked against the live
  staged buffer (`CONFLICT` on a stale read) so an apply never lands a
  draft the caller never actually reviewed; it is required whenever the
  edit changes env or a managed-service binding. An edit that changes env
  (the top-level `[env]` table or any container, job or task `env` table)
  or a managed-service binding (read off a rolled-back dry run of the
  reconcile, or declared on an app that has no environment yet, which the
  first deploy's environment bootstrap would reconcile unchecked) is gated
  the same way `setAppSecret` gates a direct secret write: a fresh session
  elevation, or, when the app requires secret approval, every changed
  `[env]` key must match an applied secret-change proposal, and anything
  else is refused except a managed-service release (remove or detach).
  Attaching or rebinding a project managed service
  also needs `project.update` on the project, the permission
  `attachProjectManagedService` checks; `registerApp` applies the same
  check to an inline manifest. A manifest the reconcile rejects returns
  `VALIDATION`, including a project-scoped service on an app that belongs
  to no project. The audit entry names the changed keys and bindings, never
  values or digests, and the returned manifest text masks `[env]` values for
  a caller who can't reveal secrets, as every other manifest response does
  (#1920). The manifest editor now shows an "Apply" button
  (disabled while the draft has unsaved local edits) and a "Staged, not
  applied" badge instead of "Push to repo" when the app has no source repo,
  and confirms before applying, listing the env key names (never values)
  the server reports the draft changes (`stagedEnvChanges`) (#1759).

- Honour per-container `dockerfile_path` / `build_context` from
  `[[workloads.containers]]` when building an app's image. The build
  previously only ever read the app-level `RegisteredApp.dockerfile_path` /
  `build_context`, so a manifest that set a container's `build_context` to
  reach a Dockerfile outside its own directory (the monorepo shape where one
  image serves two registrations of the same repo) passed validation and
  was silently ignored (#1756). An explicit app-level value (an
  `--dockerfile-path`/`--build-context` register flag, or monorepo
  discovery) wins outright; a container's field only applies on whichever
  of the two the app hasn't itself customized, resolved against the
  manifest's own directory and rejected (parse time for an absolute path,
  build time for a resolved result that climbs above the repo root) rather
  than silently falling back. Kaniko reads `--dockerfile` relative to the
  build context, so the Dockerfile (a container's, or an app-level one under
  a container's build context) is passed relative to the effective context,
  and a Dockerfile outside that context is refused: kaniko tries such a
  path against its own working directory first, which reaches the build
  pod's filesystem. A manifest with more than one distinct non-default
  `dockerfile_path` or `build_context` across its containers is rejected at
  parse time, since exactly one image is ever built for an app, so a
  second, different value would just be the same silent drop this feature
  closes.

- Confine agent and managed-service secret locations to the organization's own
  secret namespace (#1921). Every driver files a relative ref under the
  install-wide root that every org without its own cluster shares, so
  `managed/<instance>/url` named another tenant's database secret.
  - **Agent refs.** An env spec's `secretRefs`, `upsertAgentSecretRef`, a
    manifest's `[secrets]` table, the direct-upload importer and the
    `upsert_agent_environment_spec` command accept only a location under
    `agents/<org guid>/`. `secret://` is allowed and stripped once before the
    check and the store, so `secret://agents/<org guid>/x` is the secret at
    `agents/<org guid>/x`. An ARN is refused; use the relative form. A bundle
    location and a managed-service secret are refused: attach the bundle or
    the service instead.
  - **Bundles.** An agent bundle's `backendRef` must sit under
    `agent-bundles/<org guid>/`, may not use `secret://`, and must not name a
    location another live bundle of the org holds, in any spelling. Deleting
    a bundle leaves a location another bundle still holds.
  - **Managed services.** Every secret ref a managed-service config names
    (`*_secret_ref`, `*_secret_arn`, the AWS-native `SecretArn` and
    `DomainJoinServiceAccountSecret`, the existing-S3 `credential_bundle`)
    must sit under `services/<org guid>/<owner guid>/`, where the owner is the
    service's app, or its project for a project service, and may not use
    `secret://`. Google Secret Manager references (Cloud Functions
    `secret_environment` and `secret_volumes`, Managed Kafka Connect
    `secret_paths`) must be in the install's project and carry the id the GCP
    secrets driver gives that namespace. That applies at provision, update,
    manifest persist and adopt, and again before a driver runs. A binding the
    driver copied from the config is refused when it is synced, deployed,
    mounted, granted to the app's role or injected into an agent. Refs the
    driver mints itself keep resolving.
  - **Cloud Functions identities.** A config's `service_account_email`,
    `build_service_account` or `event_trigger.service_account_email` must be
    listed in the new install policy `cloud_functions_allowed_service_accounts`.
    Empty, the default, refuses every one; omitting the field still runs the
    function as Google's default runtime account.
  - **GCP raw fields.** Cloud Functions and Managed Kafka `raw_fields` (and the
    build, service and event-trigger variants) and `clear_fields` must use the
    API's lowerCamelCase JSON field names. Google also accepts a field's proto
    name, which carried a service account or a Secret Manager project past the
    checks above. A config may not spell one field two ways.
  - **Value mutations.** `setAgentSecretValue`, `deleteAgentSecretValue` and
    `revealAgentSecretValue` act only on refs typed on the spec. An env var
    that comes from a managed-service binding is refused as managed by the
    platform.
  - **Stored locations fail closed.** A location stored before this change is
    never read, written or deleted. Spawn, agent-box start, app deploy and
    hourly rotation fail with a readable error, and so do the status probe,
    value and bundle key operations, and bundle key refresh. Removing a binding,
    deleting such a bundle (the stored value is left alone) and moving a bundle
    into the namespace still work.
  - **Migrate before upgrading.** Run the read-only
    `manage.py audit_agent_secret_namespace` to list what stops resolving,
    including the `agents/<org slug>/...` convention and bare names. Move each
    value into the namespace and point the ref there before upgrading.

- Cluster and deployment history no longer shows one org another org's rows.
  `astroliftClusterLifecycleAudit` returns only mutations run in the caller's
  org, and matches the cluster by whole value instead of by a substring of the
  variables. `MutationAuditLog` now records the org; rows written before this
  change appear in no tenant's timeline (superusers still read them through
  `auditLogs`). `bootstrapRuns` / `lastBootstrapRun` return only the runs the
  caller's org recorded; legacy runs on an org-owned cluster take that
  cluster's org. `astroliftDeploymentApprovalHistory` and `approvedBy` ignore
  audit rows another org wrote against the deployment. Both audit writers file
  a mutation under the tenant org only when the actor is an active member of
  it (or an active superuser), so a session that names another org in
  `X-Astrolift-Organization` no longer lands its refused mutations in that
  org's views. `approvedBy` lists only allowed approvals from eligible
  approvers. `astroliftClusterLifecycleAudit` caps `limit` at 200. Migration
  `core.0014` builds the new audit-log index concurrently (#1955).

- Every install now carries the stock role catalogue (#1864). Three system
  roles are new. Team Operator and Project Operator run, attach to and cancel
  the apps, agents and workflows in their scope, and change no configuration.
  Organization Viewer reads the whole organization but not its audit log. The
  rest of the Zentinelle permissions from #1888 are declared: configure,
  policy view and edit, usage view, audit view and export, status view, and
  conformance view. They gate nothing until the Zentinelle screens land, but
  the stock roles already carry the #1888 defaults. Org owners and admins hold
  all of them. Auditors get status, policy and audit view plus audit export.
  Team owners and admins, project admins, and the team and project developers
  and operators get usage and policy view. The organization viewer gets
  status. No existing role loses a permission. Migration
  `astrolift_identity.0034` applies the catalogue to existing installs.

- Install-wide operations need the platform operator: an active superuser,
  whose bearer token also needs the `admin` scope. The stock organization
  owner and admin roles hold every permission, `admin.elevate` included, so
  the permission gates on these admitted every organization's admins.
  `setFeatureFlag` flipped a runtime flag for every tenant,
  `resyncAllAstroliftCiWorkflows` swept every organization's managed apps,
  `scanCloudOrphans` listed every organization's orphaned cloud resources
  (a team owner with its team selected got there too), and `reapCloudOrphan`
  deleted IAM roles and managed services through any organization's cluster.
  Django staff who are not superusers lose the shortcuts that reached across
  organizations: the `dispatchers` list, every organization in
  `astroliftOrganizations`, editing platform workflow templates, and creating
  templates and workflow triggers (#1978).

- Django model permissions (`config/roles_gen.py`) no longer authorize app
  code. The legacy scaffold surfaces that read them now admit only the
  platform operator (an active superuser), the only caller they admitted in
  practice. Two admitted far more and are closed: the `profile` mutation let
  any logged-in user edit any user's profile, including username and active
  flag (edit your own with `updateMyProfile`), and the `/app/core/core/*`
  group and permission tools needed only a login. They now need a staff
  session plus the Django admin's own permissions. The generic `delete`
  mutation still deletes only the seven scaffold models it always could
  (#1864).

- Record an empty object instead of failing the mutation audit log when a
  GraphQL mutation is sent with no `variables`. The previous NOT NULL failure
  was only logged as a warning, but it had already poisoned the rest of the
  request's transaction (#1882).

- The App Builder files sync accepts binary files as base64 with a declared
  encoding, plus one `data_file` with its own cap (Constance
  `BUILDER_DATA_FILE_MAX_BYTES`, default 64 MiB). Promote now serves the app
  from its own namespace, with the data file on a persistent volume when the
  cluster can provision one; the response carries `app_url` and
  `data_persistent`. Dev environments provision again: the renderer no longer
  slices a UUID, and its names use the whole guid. See `docs/builder-api.md`
  (#1858).

- Deduplicate queued agent steering by an optional client request UUID and expose
  an exact receipt lookup, so a lost enqueue reply can be recovered after delivery
  or task completion without repeating the instruction (#1842).

- Prevent concurrent workflow starts from colliding on blank execution
  identifiers while creating their run records. The final Temporal workflow
  identifiers remain unchanged (#1844).

- Preserve native agent questions and tool approvals while an operator answers:
  human wait time has a separate, cumulative 24-hour allowance instead of spending
  the execution timeout. Answers resume the remaining budget, workflow stages use
  the same persisted clock, and Kubernetes reserves the allowance on the existing
  Job before acknowledging a question. Expired task cleanup retries until the
  exact container is gone (#1840).

- API-token organization discovery lists only the token's issuing organization.
  HTTP requests and terminal WebSocket handshakes reject a conflicting selected
  organization before resolving a target. Matching selections and clients without
  an explicit organization continue to use the token's organization (#1791).

- Keep multiple terminal sessions responsive by waking exec-stream readers on
  their event loop instead of parking shared executor threads. Cancelling a
  reader no longer consumes output meant for its replacement (#1783).

- Return an error when explicit agent-box destruction cannot delete its cluster
  objects. The box retains its status and remains visible with the teardown
  error, so the operator can retry instead of losing track of a running pod
  (#1780). Delete Jobs with foreground propagation so their running pods are
  removed instead of orphaned.

- Read every recorded stage attempt of an exact workflow execution through a
  paginated API with organization and project permissions. Approval history,
  errors, and linked runs remain readable without Temporal; scoped cursors
  prevent an inspection from switching executions (#1804).

- Expose exact workflow execution reads, cancellation, termination, and cleanup
  retries for both configured workflows and direct definition runs. Reads accept
  the dispatch record ID or GUID, enforce its organization and project permissions,
  and report Temporal closure separately from resource cleanup. Controls pin both
  Temporal IDs and refuse unverified executions (#1801).

- Clean up explicitly owned agent tasks after a workflow closes, preserving
  their original cluster, namespace, or Docker daemon across retries. Stop
  confirms resource deletion before settling the task and exposes pending or
  failed cleanup for later reconciliation. Cancellation during dispatch no
  longer starts another stage attempt; older Temporal histories still replay
  (#1797).

- Use complete task GUIDs for Kubernetes Job and local Docker container names,
  preventing parallel tasks created in the same millisecond from sharing a
  resource. Existing tasks continue using their saved external IDs (#1799).

- Remove duplicate managed-domain operation imports after concurrent revalidation
  fixes merged, restoring the production frontend build.

- Type managed-domain revalidation with the generated GraphQL operation so the
  domain console passes the production frontend build.

- Dispatch fleet and runtime reads return their token-authenticated organization
  and dispatcher identity. `scope=dispatcher` filters fleet tasks to that
  dispatcher, allowing Client Cove to bind a connection to one deployment.
  Existing organization-wide reads remain the default. Fleet responses report
  truncation with `has_more`; deleted organizations cannot use these reads.

- `startDeployment` accepts an omitted image tag for platform builds and apps
  using manifest images. Platform builds resolve the deploy branch (or the new
  `sourceRef` input) to a commit and use its SHA as the default image tag before
  approval or scheduling. Immediate and approved workflows receive the recorded
  commit, and manifest resync fetches it. CI-pushed apps still require a tag;
  source lookup failures leave existing deployments untouched (#1737).

- Close #1365 with explicit, separately authorized adoption of an existing
  Azure resource. The fail-closed ownership contract shipped in #1443 / #1446
  refuses every mutating path against a resource whose identity tags do not
  name the calling managed service, teardown included, which left anything
  provisioned before its driver stamped an identity tag removable only by
  hand — a bill the platform cannot stop. `adoptManagedResource` is the
  migration path: it reads the resource's markers, records them, and stamps
  the same envelope `arm_tags_for` writes on provision, so the resource is
  ordinary afterwards. It carries its own grant, `managed_service.adopt`,
  rather than reusing the provisioning grants, because booking a service and
  taking over somebody else's resource are different capabilities; migration
  `astrolift_identity/0023` re-upserts the system roles for it. Every attempt
  writes a `ManagedResourceAdoption` row — actor, reason, resource, and the
  prior ownership markers verbatim, since "it had no tag" and "it had another
  service's tag" are different things to have approved and the cloud forgets
  the difference the moment the envelope is merged. Taking a resource from
  another managed service additionally requires naming that owner in the
  request. Refusals are recorded too.
- Delete API Management's `adopt_existing` config flag and its
  `apim_allow_adoption` install policy. It was the last route by which a name
  collision could become a takeover with no record of what was taken, which is
  the hazard #1365 exists to remove; the config validator now rejects the key
  rather than ignoring it, so an operator who asks for the old behaviour is
  told. The driver's adopted-resource teardown guard and its `delete_adopted`
  escape hatch survive and become the first consumer of the shared
  `astrolift-adopted` marker the new operation writes.
  A reconcile also stops replacing the service's tag map wholesale, which
  would otherwise have stripped that marker — and the operator's own tags —
  off an adopted service on the next provision.

- Make `cache` reachable on every cloud with an in-cluster Memcached variant,
  `k8s_native/cache/memcached`. The kind was executable on AWS only: GCP sells
  Memorystore for Memcached, Azure sells nothing equivalent, so one in-cluster
  variant covers both holes instead of one of them. It emits the same envelope
  the ElastiCache Memcached variants emit, `CACHE_NODES` included, so a
  manifest moves between them without the app reading different variables.
  Memcached authenticates nobody, so the driver always ships an ingress
  NetworkPolicy scoped to the app's own namespace and its organization's agent
  namespace; there is no switch to turn that off. The `cache/gcp` and
  `cache/azure` rows are gone from the declared-gap ledger.

- Let the exec relay reach an agent box. `/app/exec/<target>/<pod>` resolved
  its first path segment against `RegisteredApp` only, so a healthy running
  box closed the handshake with the same code a real permission denial uses
  and the operator was told to go ask for `app.exec_pod`, which they already
  held. The relay now resolves a box as a box — gated on the box's own
  organization, and on a new `agent_box.attach` grant seeded wherever
  `agent.dispatch` is, so whoever may start a box may reach it. The exec
  backend's cluster lookup learned the same target, since the CLI resolves a
  pod before it dials. A slug that resolves to nothing now closes 4404
  instead of 4403, so "no such target" and "you lack the grant" stop reading
  alike; both are still pre-accept closes, so the handshake's HTTP status is
  unchanged. `AgentBox.last_attached_at` is finally written, when a session
  opens. It remains advisory telemetry: idleness is still measured in-pod by
  tmux, which sees a client detach the instant it happens.
- Give agent-box pod resolution its own field, `agentBoxPods(slug)`, behind
  `agent_box.attach`. Routing it through `astroliftAppPods` put it behind
  `app.read_logs`, which `app_deployer` — the one role holding
  `agent.dispatch` without it — does not have, so the role this ticket is
  about could start a box, be granted attach, and still never resolve a pod
  to dial. `require_permission` ANDs, so admitting the box grant on the app
  resolver would have meant weakening the app-pod gate to fix a box problem.
  `astroliftAppPods` answers for registered apps again and nothing else.
- Write `AgentBox.pod_name`. It was declared, projected onto the GraphQL type
  and set by nothing, so the pod column rendered blank and every attach fell
  through pod resolution even for a box that had been warm for an hour. The
  reaper stamps it when it observes the box running and clears it on restart,
  so a name never outlives the pod it points at. It is a fast path, not a
  source of truth: it is blank until the first sweep after the pod comes up,
  and pod resolution stays the fallback.
- Stop reporting managed-service config changes as applied when the driver
  cannot apply them. Fifteen drivers (S3, CloudFront, GCS, Pub/Sub, Blob x2,
  Service Bus, CNPG, MySQL/MongoDB/RabbitMQ/Redis/Strimzi/NATS operators, NFS)
  implemented `update()` as a no-op returning `ok=True` while inheriting the
  permissive `editable_fields()` default, so the update workflow advanced
  `appliedConfig` and returned the row to ACTIVE over an unchanged resource.
  They now declare no editable fields, which makes `updateManagedService`
  reject the change at the API boundary and point at `reprovisionManagedService`
  before any workflow starts, and refuse with a permanent, non-retryable error
  if the driver is reached anyway. A cross-provider contract test holds every
  registered driver to it: claiming editable fields requires an `update()` that
  can apply them, and claiming none forbids reporting success.
- Emit the canonical connection envelope from six drivers that were missing
  most of it. `azure/postgres/azure_pg_flex` and `k8s_native/postgres/cnpg`
  shipped only the pre-#1003 `DATABASE_*` names and no `POSTGRES_*` at all;
  `aws/mysql/rds_mysql` and `azure/mysql/azure_mysql_flex` likewise had no
  `MYSQL_*`, so an app that moved between providers of the same kind silently
  lost every variable it read. `aws/faas/lambda` and
  `gcp/faas/cloud_functions_gen2` now emit `FUNCTION_ARN` and
  `FUNCTION_REGION` like their Azure and Knative siblings. All six changes are
  additive: the legacy `DATABASE_*` names stay in place as aliases reading the
  same value, so nothing a deployed workload reads today disappears.

- Publish eleven connection-envelope keys that every driver of their kind
  already emitted. `email` gains `EMAIL_FROM_ADDRESS` and `EMAIL_REGION`,
  `encryption_key` gains `ENCRYPTION_KEY_SPEC` / `_USAGE` / `_MULTI_REGION`,
  `mq` gains `MQ_AUTH_STRATEGY`, `workflow_engine` gains
  `WORKFLOW_ENGINE_TYPE`, and `private_endpoint` gains
  `PRIVATE_ENDPOINT_DNS_NAMES`, `_NETWORK_INTERFACE_IDS`, `_SERVICE_NAME` and
  `_TYPE`. They looked portable to an app author and were not: the kind's
  published `envelope_keys`, which the service catalog serves to the UI and
  the API and which `revealManagedServiceConnection` walks, did not list them.
  None of the eleven is a credential, so reveal shows them unmasked.

- Make the binding-envelope guardrail read conditional bindings, and separate
  a value's _encoding_ from its _provenance_. `binding()` bodies branch, and
  reading only the last branch mis-reported three ledgers at once: the Aurora
  postgres drivers read as emitting the MySQL envelope and none of
  `POSTGRES_*`, and every AWS/GCP Redis driver's `REDIS_URL` was recorded as
  whichever of its secret-reference and literal branches came last in the
  source. Guards of the form `self.<attr> == <const>` are now resolved against
  the concrete driver class, so dead branches drop out and a key reachable on
  no branch is not counted as emitted. A secrets-backend reference is opaque
  rather than an encoding, so it no longer collides with `json` or a
  delimiter-joined list. Two drivers emitted a genuinely different encoding
  from their siblings and now match, byte-for-byte identically:
  `aws/cache/elasticache_serverless_memcached`'s `CACHE_NODES` is a
  comma-separated endpoint list, and `gcp/redis/memorystore_valkey`'s
  `REDIS_CA_CERT` is a newline-joined PEM chain on both of its CA branches.

- Carry human-gate state on a workflow run's stage rows.
  `workflowStageExecutions` now returns `stageRole`, `stageApprovers`,
  `humanGateState` (`pending` / `approved` / `rejected` / `closed`, empty for
  non-gate stages), and `humanGateNote`, so a remote client can render
  "waiting on approval" and who it waits on as read-only platform truth
  instead of reverse-engineering the execution's `output` JSON. The resolver
  also fails closed with no tenant: its read scope is org UNION
  platform-global, which for a null org matched every org-less run in the
  install.

- Add a steering channel into a running agent task: `sendAgentTaskInput`
  queues a follow-up prompt against an `AgentTask`, and the runner consumes
  it at its next turn boundary through the state callback it already posts.
  Deny-by-default behind the new `agent_task.send_input` permission,
  fail-closed org scoping, audited, and delivered at most once in order.

- Add executable Google Cloud CDN lifecycle support, including Cloud Storage,
  NEG, instance-group, and existing backend-service origins; safe cache policy
  controls; a managed global HTTP(S) load-balancer graph; managed or external
  TLS; Cloud Armor policies; invalidation; signed-URL key rotation; portable
  bindings; ownership/adoption; and protected deletion.
- Add executable Google Cloud Private Service Connect consumer endpoints for
  regional published services and global Google APIs/VPC Service Controls,
  with managed or external addresses, Shared VPC references, Service Directory
  registration, global access, portable bindings, adoption, and protected
  teardown.

### Added

- Add Azure Private Endpoint lifecycle with explicit target, subnet, Private
  DNS, and manual-approval policy gates; keyless bindings on the canonical
  `private_endpoint` envelope shared with the AWS and GCP drivers;
  ownership-safe reconciliation; and deletion-protected teardown.
- Add Google Cloud Workflows lifecycle with YAML/JSON definitions, revision
  snapshots and rollback, service identities, CMEK, environment variables,
  call logging, execution history, invocation bindings, execution controls,
  boundary-safe adoption, and destructive-history-aware teardown.
- Add Azure SQL Database provisioned, serverless, and Hyperscale variants plus
  Azure SQL Managed Instance with typed ARM requests, VNet/subnet isolation,
  Key Vault-backed portable bindings, retention controls, database-copy
  snapshot/restore, and fail-closed teardown semantics.
- Add Microsoft.FileShares provisioned-v2 NFS lifecycle with SSD capacity,
  Local or Zone redundancy, provisioned performance, root-squash and encrypted
  mount controls, subnet allowlists, portable AZNFS bindings, child snapshots,
  ownership-safe reconciliation, and protected data-loss-aware teardown.
- Add Google Cloud Run functions (Cloud Functions v2) lifecycle with Cloud
  Build source declarations, HTTP and Eventarc triggers, Secret Manager
  environment/volumes, scaling, private networking, Binary Authorization,
  CMEK, authenticated IAM bindings, adoption, and guarded teardown.
- Add preview project observability bundles on a shared
  kube-prometheus-stack with namespace-scoped ServiceMonitor and PodMonitor
  targets, safe standard alerts and dashboards, policy-gated custom content,
  authenticated Grafana bindings, operator-gated Prometheus query access,
  operator-only Alertmanager, ownership-safe updates, and non-destructive teardown.
- Add preview adoption of existing S3-compatible buckets on Kubernetes with
  project-scoped ownership records, org-scoped external credential bundles,
  portable S3 bindings, endpoint/TLS/path policy gates, safe update/unlink,
  and explicit retention of the external bucket, objects, and credentials.
- Add preview KServe 0.20 model endpoints for Kubernetes installs with the
  complete native InferenceService predictor, transformer, explainer, model,
  canary, accelerator, scheduling, scaling, and storage surface; Standard-mode
  private/read-only defaults; portable REST and gRPC bindings; managed runtime
  identity; exact-UID adoption; protected teardown; and install-level policy
  gates for exposure, images, runtimes, storage, logging, tokens, pod security,
  caches, deployment modes, autoscalers, and replica limits.
- Add preview Argo Workflows 4.1 WorkflowTemplate resources for Kubernetes
  installs with native DAG/step/template specs, managed CronWorkflows and event
  bindings, least-privilege executor bootstrap, bounded parallelism/deadlines,
  portable submit bindings, UID-pinned modular references, declarative
  snapshots, run-retaining teardown, ownership-safe pruning, and install-policy
  security gates.
- Add preview Knative Eventing brokers for Kubernetes installs with
  declarative Triggers, CloudEvents ingress bindings, delivery and dead-letter
  policy, broker-class and destination guardrails, live readiness, explicit
  adoption and ownership-safe Trigger pruning and teardown.
- Add preview Kubernetes Gateway API 1.5 managed gateways with complete
  listener, address, infrastructure, HTTP, gRPC, TLS, TCP, and UDP route
  shapes; Backend TLS policies and ListenerSets; live Programmed and route
  state; portable bindings; explicit adoption and pruning; and install-level
  cross-namespace, extension, custom backend, and experimental-protocol
  guardrails that preserve destination-owned ReferenceGrant boundaries.
- Add preview Knative Serving functions for Kubernetes installs with
  scale-to-zero presets, revision traffic splitting, private-by-default routes,
  digest-pinned images, secret and workload controls, live readiness and route
  bindings, install-policy guardrails, explicit adoption, and ownership-safe
  teardown.
- Add preview OpenSearch Operator search and vector services for Kubernetes
  installs with OpenSearch 3.8, secure 3.x CRDs, tenant-scoped users and
  NetworkPolicies, digest-pinned non-root vector-index bootstrapping, explicit
  HNSW mappings, protected storage teardown, and external secret bindings.
- Add preview Kubernetes SQL Server 2025 Express managed services with
  non-root StatefulSets, durable PVCs, external credential bundles, encrypted
  TDS bindings, guarded LoadBalancer exposure, safe expansion, and optional
  crash-consistent CSI snapshots.
- Add preview SeaweedFS Operator object storage for Kubernetes installs with
  shared-cluster bucket reconciliation, bucket-scoped S3 IAM credentials,
  versioning, Object Lock, quotas, placement controls, explicit anonymous
  reads, truthful readiness, and data-safe retain/delete teardown.
- Add Kubernetes StorageClass and Rook CephFS project volume templates with
  consumer-namespace dynamic claims, live StorageClass/CSI preflight,
  portable app and agent mounts, data-safe teardown, and full catalog,
  GraphQL, and project-resource UI visibility.
- Add portable managed-filesystem runtime attachments for applications and
  agent Jobs, including CSI/PVC preflight, credential-reference projection,
  provider-specific EFS/FSx/NFS mounts, owned storage cleanup, and safe mount
  readiness metadata in the project resources UI and GraphQL API.
- Add Google Cloud Filestore shared filesystems across Basic, Zonal,
  Regional, and Enterprise tiers with NFSv3/v4.1, PSC and IPv6, export ACLs,
  Kerberos directory integration, CMEK, custom performance, deletion
  protection, replication health and promotion, native snapshots, regional
  backups, restore, portable bindings, and adoption-safe teardown.
- Add Google Cloud Operations observability bundles with declarative Cloud
  Logging buckets, views, sinks, log metrics, exclusions, scopes, and saved
  queries and Log Analytics links plus Cloud Monitoring dashboards,
  notification channels, alert policies, nested resource groups, uptime
  checks, metric descriptors, services, and SLOs. Preserve provider-native
  request bodies, resolve credentials and verification codes from Secret
  Manager, and enforce ownership-, dependency-, and data-safe teardown.
- Add Azure Managed Redis lifecycle for Balanced, Memory Optimized,
  Compute Optimized, and Flash Optimized tiers with TLS-only bindings,
  Key Vault-backed access keys, persistence/modules, CMK rotation,
  geo-replication links, additive Entra access assignments, scaling, status,
  and fail-closed private-networking and data-preserving teardown semantics.
- Expose Azure Service Bus topics and default subscriptions through the
  cloud-neutral `topic/service_bus_topic` lifecycle and portable topic binding,
  while retaining the existing queue-shaped alias.
- Add explicit Azure Cosmos DB for NoSQL, MongoDB RU, Gremlin,
  Cassandra, and Table API variants with API-correct resource lifecycle,
  throughput and autoscale controls, multi-region placement, continuous or
  periodic backup, point-in-time restore, CMK encryption, network rules,
  Key Vault-backed portable bindings, ownership-safe reconciliation, and
  protected teardown.
- Add Azure Event Hubs native-stream and Kafka-compatible variants with
  namespace/event-hub lifecycle, Entra workload bindings, consumer groups,
  retention and compaction, Capture, scaling, networking, CMK, and guarded
- Add Azure Event Grid Standard namespace topics with CloudEvents publishing,
  pull and supported push subscriptions, retention, filters, dead-lettering,
  workload-identity delivery, Key Vault-backed pull credentials, explicit
  ownership, and guarded lifecycle management.
- Add an Azure Event Grid custom-topic event-bus driver with Entra workload
  bindings, push destinations, event and advanced filters, batching, retries,
  dead-lettering, selected-network controls, identity delivery, and guarded
  destructive teardown.
- Add the preview `filesystem/azure_files_classic` driver for storage-account
  Azure Files SMB/NFS shares, including network ACLs, per-protocol encryption,
  dual Key Vault-backed SMB rotation keys, snapshots, soft delete, guarded
  teardown, and portable mount metadata.
- Add the preview `faas/azure_functions` driver for Linux Azure Function Apps
  on operator-owned plans, with identity-first Flex zip and Premium/Dedicated
  container deployment, immutable digest-pinned artifacts, exact dependency
  allowlists, least-privilege storage/ACR grants, credential-free portable
  bindings, deletion protection, and convergent ownership-safe lifecycle.
- Add preview Azure API Management lifecycle with explicit SKU/capacity,
  public or allowlisted internal networking, managed identities, typed APIs,
  operations, backends, subscriptions and Key Vault-backed custom domains,
  generated policy allowlists, keyless portable bindings, ownership-safe
  pruning/adoption, and protected declarative teardown.
- Add Azure SQL Database provisioned, serverless, and Hyperscale variants plus
  Azure SQL Managed Instance with typed ARM requests, VNet/subnet isolation,
  Key Vault-backed portable bindings, retention controls, database-copy
  snapshot/restore, and fail-closed teardown semantics.
- Add Microsoft.FileShares provisioned-v2 NFS lifecycle with SSD capacity,
  Local or Zone redundancy, provisioned performance, root-squash and encrypted
  mount controls, subnet allowlists, portable AZNFS bindings, child snapshots,
  ownership-safe reconciliation, and protected data-loss-aware teardown.
- Add Google Cloud API Gateway lifecycle with immutable OpenAPI and gRPC
  config revisions, zero-downtime gateway retargeting, backend service
  identity, revision retention, portable endpoint bindings, adoption,
  snapshots, and guarded dependency teardown.
- Add Google Managed Service for Apache Kafka lifecycle across clusters,
  topics, ACLs, consumer offsets, Schema Registry, Connect clusters and
  connectors, including PSC networking, CMEK, mTLS, IAM/portable bindings,
  guarded adoption, data-loss confirmations, and integration observability.
- Add a project-shared Google Eventarc event fabric across Advanced message
  buses, pipelines, enrollments, Google API sources, direct publishing, event
  transformation and format conversion, plus Standard triggers and partner
  channels, with CMEK, IAM bindings, guarded adoption, ownership-safe pruning,
  and dependency-aware teardown.
- Add Memorystore for Valkey lifecycle with private PSC endpoints, IAM and
  Preview token authentication, Secret Manager-backed two-phase token
  rotation, TLS CA bindings, every current node type, cluster and non-cluster
  modes, scaling, RDB/AOF persistence, CMEK, scheduled and on-demand backups,
  exact restore, cross-instance replication, adoption, and protected teardown.
- Add Cloud Spanner Graph lifecycle on Enterprise and Enterprise Plus with
  GoogleSQL/GQL property graphs, fixed or autoscaled capacity, atomic custom
  schemas, replay-safe DDL, CMEK, exact backups and restore, portable IAM
  bindings, database ownership markers, and adoption-safe teardown.
- Add Google Cloud SQL for SQL Server 2017–2025 across Express, Web,
  Standard, Enterprise, and Enterprise Plus configurations with real Admin API
  operation polling, provisioned databases, PITR, HA, data cache, CMEK, exact
  backups and restore, guarded storage shrink, Secret Manager-backed portable
  bindings, and adoption-safe teardown.
- Add Firestore Native managed document databases with Standard and Enterprise
  editions, IAM-first portable bindings, CMEK and deletion protection, PITR
  cloning, scheduled backups, GCS exports, restore, composite/vector/search
  indexes, field index overrides, TTL, and adoption-safe teardown.
- Add Google BigQuery warehouse lifecycle for datasets, native dataset
  controls and CMEK defaults, reservations, assignments, explicit capacity
  commitments, workload-identity bindings, billing estimation, health, and
  ownership/data-safe teardown.
- Add GCP Pub/Sub topic lifecycle with rotatable CMEK, retention and
  residency, schemas, managed ingestion, message transforms, declarative
  pull/push/BigQuery/Bigtable/Cloud Storage subscriptions, workload bindings,
  export health, ownership-safe pruning and teardown, and full reconciliation.
- Add Google Cloud AlloyDB for PostgreSQL lifecycle with private, PSC, and
  public connectivity, primary and read-pool sizing, native cluster/instance
  controls, continuous and on-demand backups, restore, Secret Manager-backed
  portable bindings, and protected teardown.
- Add Amazon Kinesis Data Streams lifecycle with on-demand and provisioned
  capacity, retention, encryption, enhanced monitoring, warm throughput,
  large records, policies, enhanced fan-out consumers, portable bindings,
  and protected data-loss-aware teardown.
- Add Amazon Data Firehose lifecycle with every current AWS source and
  destination request shape, mutable destination updates, customer-managed
  encryption rotation, scoped role grants, portable bindings, and protected
  buffered-record-aware teardown.
- Add Amazon EventBridge custom event-bus lifecycle with KMS/DLQ/log
  controls, resource policies, declarative rules and full target parameters,
  archives, pruning, portable publisher bindings, and protected teardown.
- Add Amazon SNS standard and FIFO topic lifecycle with KMS encryption,
  high-throughput FIFO, archives, declarative subscriptions, filtering,
  dead-letter queues, replay, and least-privilege portable bindings.
- Expand Amazon SQS lifecycle with portable bindings, KMS or SQS-managed
  encryption, policies, dead-letter/redrive controls, long polling, FIFO
  throughput settings, safe non-empty teardown, and access-mode IAM grants.
- Add Amazon Redshift provisioned and Serverless warehouses with private
  networking, IAM-first bindings, managed admin secrets, capacity controls,
  Data API grants, snapshots, restore, and protected teardown.
- Add Amazon Neptune provisioned and Serverless graph databases with private
  networking, IAM SigV4 bindings, Gremlin/SPARQL/openCypher endpoints, scaling,
  snapshots, restore, global-cluster inputs, and protected teardown.
- Add Amazon Keyspaces Cassandra-compatible tables with on-demand or
  provisioned capacity, multi-Region keyspaces, IAM SigV4 bindings, encryption,
  TTL/CDC controls, 35-day point-in-time recovery, and restore lifecycle.
- Add AWS DocumentDB provisioned and Serverless v2 clusters with private
  networking, encrypted storage, portable Mongo-compatible bindings, scaling,
  backups, snapshot restore, deletion protection, and convergent teardown.
- Add private-by-default OpenSearch Serverless search and vector collections
  with cluster-scoped VPC endpoints, encryption/network/data policies,
  SigV4 workload grants, and explicit destructive-delete acknowledgement.
- Add AWS ElastiCache Serverless for Valkey, Redis OSS, and Memcached,
  node-based Valkey and Memcached, and durable MemoryDB with portable cache/Redis bindings, private
  networking, RBAC/password/IAM authentication, sizing, updates, snapshots
  where supported, and convergent teardown.
- Add AWS Aurora PostgreSQL/MySQL provisioned and Serverless v2 clusters,
  every supported RDS SQL Server edition, and RDS Proxy with secret or
  end-to-end IAM authentication, pool tuning, portable bindings, and
  convergent teardown.
- Add project-owned managed databases, caches, search, object storage,
  queues, and secret bundles with explicit app-environment and agent-recipe
  attachments, provider-backed secret CRUD/reveal, and runtime injection.
- Add a cluster-derived, multi-cloud project resource catalogue that exposes
  executable provider options and visible, issue-linked roadmap capabilities
  with portable sizing, native configuration schemas, and fail-closed validation.
- Add project-owned workflow topology and execution views across project,
  workflow, history, and role-aware dashboard surfaces.
- Add composable nested workflow stages with bounded cycle-safe resolution,
  linked Temporal child execution, run lineage, and graph drill-down.
- Add deterministic GraphQL SDL and MCP capability-superset exports with
  runtime-handler, JSON-Schema, frontend-codegen, and CI drift guardrails.
- Add source-reconciled `workflows/**/*.toml` definitions and runnable chained-agent
  stages with environment, prompt, skill, output-key defaults, configured binding
  overrides, immutable task packets, named structured outputs, and a worked example.
- Add canonical modular agent packages, repository slice/federation discovery,
  AGENTS.md/Langflow/Flowise imports, and authenticated MCP agent and shared
  project-resource operations.
- Add agent secret-reference CRUD, explicit reveal, reusable secret bundles,
  attachment precedence, provider capability reporting, and management UI.
- Add operator kill controls, task deadlines, callback authentication, and
  application log visibility for agent runs.

### Changed

- Apply managed-service configuration changes through durable Temporal update
  workflows, retain the last provider-confirmed configuration, and expose the
  current operation/run identifiers and timestamps in GraphQL and project UI.
- Make managed-service secret bundle selectors portable across AWS Secrets
  Manager, Google Secret Manager, Azure Key Vault, and Vault, and fail closed
  instead of choosing an arbitrary value from a multi-key bundle.
- Mark the archived community MinIO Operator as deprecated in the Kubernetes
  resource catalogue and expose commercial AIStor and existing
  S3-compatible endpoint adoption as explicit planned variants.
- Enable every registered Azure managed-service driver in the production image
  and lifecycle resolver, install its current management/data SDKs, preserve
  provider controls at runtime, and fail closed when snapshot or retained-data
  semantics cannot be fulfilled.
- Enable every registered Azure managed-service driver in the production image
  and lifecycle resolver, install its current management/data SDKs, preserve
  provider controls at runtime, and fail closed when snapshot or retained-data
  semantics cannot be fulfilled.
- Build every Azure non-cluster capability with its driver-specific config,
  make managed-service Key Vault references unambiguous and same-vault, keep
  legacy managed bindings readable, and fail workload rendering closed when a
  managed credential resolves missing or empty.
- Make project dashboards, navigation, and repository workflow detail pages
  workload-aware, graph-first, fully linked, and observable without requiring
  a configured workflow wrapper.
- Treat repository-imported workflow definitions as first-class runnable
  project workflows; configured wrappers remain optional for custom bindings,
  inputs, and triggers.
- Issue CLI device credentials with narrow workflow write and trigger scopes.
- Issue CLI device credentials with narrow project write scope for shared
  resource lifecycle and attachment management.
- Keep backend CI below its ten-minute ceiling by running the migration graph
  against tuned disposable Postgres instances and splitting agent tests by file
  while retaining production-faithful schema setup in every shard.
- Select coupled real-Postgres shards from pull-request impact, split the
  slowest runtime suites for parallel execution, and reserve the full backend
  regression matrix for `main` and manual runs.
- Make managed GitHub workflows latest-wins and give each registered monorepo
  agent an independent package-sync workflow.
- Clarify that agent repository pushes sync immutable packages but never start
  an agent run or deploy a standing application.

### Fixed

- Name the Google Managed Kafka mTLS binding keys literally instead of
  assembling them from a loop variable. `EVENT_STREAM_CLIENT_CERT`,
  `EVENT_STREAM_CLIENT_KEY`, and `EVENT_STREAM_CA_CERT` are now readable in the
  source, so the driver drops out of the binding-envelope guardrail's
  unreadable ledger and every registered driver in all four plugins is covered
  with no exceptions. The emitted keys and values are unchanged.
- Emit `FILESYSTEM_TLS` from the dynamic PVC and Rook CephFS filesystem
  bindings, the only filesystem drivers that omitted it. The value is a
  conservative `false`: in-transit encryption belongs to the StorageClass's
  provisioner and the driver cannot observe it, and over-reporting would tell a
  consumer its traffic is protected when it may not be.
- Guard the managed-service binding envelope across every registered
  `(kind, variant)` driver in all four provider plugins, rather than three
  hand-written AWS cases. The contract now fails a driver that emits an
  envelope-prefixed key missing from the canonical envelope, that omits a key
  its same-kind siblings emit, or that encodes a shared key differently from
  them. Pre-existing divergences are recorded as exact, issue-referencing
  ledger entries that fail on both regression and repair.
- Attach Microsoft.FileShares NFS shares to workloads as real CSI volumes, read
  their encryption-in-transit state through the generated SDK's string enums so
  encrypted shares no longer publish `notls`, size classic Azure Files volumes
  from the actual share quota instead of the portable 1Gi default, and reduce
  fstab-only and CSI-driver-owned NFS options out of the CSI mount.
- Probe live Kubernetes versions, CRDs, and operator releases before managed
  service provision/update; fail closed with exact remediation, make readiness
  timeouts terminal, and generate collision-safe names for long tenant,
  project, application, and service identifiers.
- Serialize Azure ownership, billing, and custom ARM tags through one
  collision-safe codec so tag names satisfy Azure restrictions and cost
  actuals group by the same `astrolift-binding` key drivers emit.
- Make GKE workload identity reconcile project IAM roles on every deploy,
  annotate workload ServiceAccounts with canonical length-safe Google service
  accounts, include attached project resources, and reject inert raw grants.
- Render nested workflow definitions beneath their composed parent execution
  path instead of presenting parent and child pipelines as unrelated peers.
- Repair project affiliation for repository workflow definitions whose multiple
  stage agents belong to the same project.
- Stamp workflow-dispatched agent tasks with their project and team, attach
  direct definition runs to their definition, and safely backfill existing
  project ownership and execution history.
- Prevent agent workloads from inflating application health counts and scope
  workflow kill controls to users with workflow management capability.
- Poll workflow-stage agent Jobs in the same per-organization namespace used
  at spawn so live tasks are not failed and stripped of callback credentials.
- Preserve workflow stage environment recipes and output keys when importing
  Langflow or Flowise definitions through the GraphQL adapter.
- Include the narrow `mcp:write` scope in browser-approved CLI credentials so
  `astro agent register-repo` can create and reconcile agent definitions after
  login or refresh without requiring an admin bearer.
- Authorize CLI device sessions for agent environment-spec CRUD with a narrow
  bearer scope and enforce the dedicated environment-spec RBAC permissions.
- Replace retired Bedrock managed-agent defaults with invoked Opus 5 and
  Haiku 4.5 inference profiles, with worker-level model overrides so operators
  can respond to future retirements without rebuilding Astrolift.
- Issue browser-approved CLI credentials with narrow agent dispatch and secret
  write scopes so `astro agent dispatch`, `cancel`, and `secret set/rm` work
  after login or refresh without requiring an admin bearer.
- Reconcile immutable agent packages from shared GitHub App push webhooks
  without dispatching an agent run.
- Map API-token app scopes to agent secret status and write permissions so
  scoped CLI tokens can manage agent secret references as documented.
- Use the install's configured S3 bucket for immutable agent payloads and
  platform artifacts when no organization-specific blob driver is registered.
- Preserve resolved command tools in one-shot harness system prompts as well
  as thread-mode dispatch packets.

- Always inject and verify agent callback delivery so successful runs cannot
  silently lose findings or telemetry.
- Use public application GUIDs for managed workflow resync operations.
