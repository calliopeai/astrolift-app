# Changelog

## Unreleased

### Added

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
