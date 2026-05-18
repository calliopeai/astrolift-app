# CLAUDE.md - astrolift-api

Read [`bootstrap.md`](./bootstrap.md) first. It is the law.

## Key Rules

- No co-authorship messages in commits. Ever.
- No rebases. New commits only.
- Soft delete only on business objects. Never hard delete.
- MutationResult envelope on every mutation. `{ ok, errors, data? }`. Never raise from a mutation.
- Permission check at the top of every resolver and mutation. First line. No exceptions.
- No integer PKs in APIs. Use UUID (guid), slug, or content-addressed keys.
- Validate at boundaries. All input validated at API entry points.

## Patterns

- Models inherit `Tracking` or `BaseCoreModel` from `core.models`
- Admin classes inherit `BaseCoreAdmin` from `core.utils.admin`
- GraphQL schema per app: `appname/schema/{types,queries,mutations}.py`
- Schema merged in `config/schema.py`
- Workflows use Temporal Python SDK (`workflows/temporal/`)
- Feature toggles via `config/features.py` and environment variables
- Domain app config discovery via `astrolift_config/settings.py` pattern
- Provider plugins live at `backend/providers/` (full subtree, with history,
  formerly the standalone `astrolift-providers` repo). The Dockerfile
  installs it with `pip install ./providers[aws,k8s]` at image build.
  Driver protocol contracts live in `backend/providers/_sdk/`; per-cloud
  impls under `backend/providers/{aws,gcp,azure,k8s_native}/`.
