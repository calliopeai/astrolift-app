# Claude — Astrolift App

Primary conventions doc: [`bootstrap.md`](bootstrap.md)

Read it before working on any code. For backend-specific or frontend-specific conventions, also read the matching repo's bootstrap (if present) inside `backend/` or `frontend/`.

## Quick reference

- No co-authorship trailers in commits, ever.
- New commits only — no rebases, no `--amend`.
- Backend tests against real Postgres + real Temporal. Don't mock.
- Comments explain why, not what. Default to no comments.
- Schema is the contract: `make schema` after backend GraphQL changes, then frontend codegen.
- One PR can land backend + frontend changes together.
