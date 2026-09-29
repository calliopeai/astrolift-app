# Changelog

## Unreleased

- CLI and IDE sign-in credentials can discover and attach to agent boxes when
  account permissions allow it. The existing `mcp:dispatch` scope now includes
  `agent_box.attach`; read-only tokens and organization/RBAC boundaries remain
  enforced (#2188).

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
  a value's *encoding* from its *provenance*. `binding()` bodies branch, and
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
