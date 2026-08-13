# Changelog

## Unreleased

### Added

- Add project-owned managed databases, caches, search, object storage,
  queues, and secret bundles with explicit app-environment and agent-recipe
  attachments, provider-backed secret CRUD/reveal, and runtime injection.
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
  AGENTS.md/Langflow/Flowise imports, and authenticated MCP agent operations.
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
- Shard backend tests across four balanced real-Postgres jobs, retain one
  stable aggregate gate, and stop rerunning the dedicated provider suite in
  the backend pass.
- Select coupled real-Postgres shards from pull-request impact, split the
  slowest runtime suites for parallel execution, and reserve the full backend
  regression matrix for `main` and manual runs.
- Make managed GitHub workflows latest-wins and give each registered monorepo
  agent an independent package-sync workflow.
- Clarify that agent repository pushes sync immutable packages but never start
  an agent run or deploy a standing application.

### Fixed

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
