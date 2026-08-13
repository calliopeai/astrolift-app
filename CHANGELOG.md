# Changelog

## Unreleased

### Added

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

- Make managed GitHub workflows latest-wins and give each registered monorepo
  agent an independent package-sync workflow.
- Clarify that agent repository pushes sync immutable packages but never start
  an agent run or deploy a standing application.

### Fixed

- Preserve workflow stage environment recipes and output keys when importing
  Langflow or Flowise definitions through the GraphQL adapter.
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
