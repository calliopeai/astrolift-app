# Generated public contracts

These files are generated from runtime-owned definitions and committed so
clients, documentation builds, and tagged releases can consume immutable
contracts without starting Astrolift.

| Artifact | Runtime source |
|---|---|
| `../schema.graphql` | Strawberry schema assembled in `config/schema.py` with every feature enabled |
| `mcp-tools.json` | MCP metadata and authorization requirements in `astrolift_agents/mcp_contract.py` |
| `push-notifications.json` | Push data contract from `astrolift_operations/notification_dispatch.py` and the push drivers in `providers/` |

Run `make contracts` from the repository root to regenerate them and
`make contracts-check` to verify the checked-in copies. CI also regenerates
frontend GraphQL client types and rejects drift.

`push-notifications.json` needs a database, so `make contracts` does not write
it: `astrolift_operations/tests/test_push_contract_2161.py` generates it from
the real dispatcher and drivers, fails in the backend test run when the copy is
stale, and rewrites it with `ASTROLIFT_WRITE_PUSH_CONTRACT=1`. The contract
itself is described in `docs/mobile-push-contract.md`.

`mcp-tools.json` is the installation capability superset. A caller sees only
the tools allowed by both its bearer-token scope and its user's RBAC grants.
The artifact never represents a particular caller's authorization.

The repository does not publish inferred OpenAPI or `astrolift.toml` JSON
Schema files yet. Most REST views and both TOML parser families still express
parts of their contracts imperatively; generating schemas from route names or
dataclass annotations would overstate what the server validates. Those
artifacts should be added when their runtimes consume shared typed contracts.
