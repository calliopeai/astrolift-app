# astrolift-app

Astrolift control plane: backend (Django + Strawberry GraphQL + Temporal) + frontend (Next.js + React + Apollo) in a single repo, single-repo-style.

See [`bootstrap.md`](bootstrap.md) for conventions.

## Quick start

```bash
./bootstrap.sh        # one-time setup
./run.sh up           # start the full stack
```

URLs:

- Frontend:  http://localhost:3000
- Backend:   http://localhost:8000/app/
- GraphQL:   http://localhost:8000/app/gql/config/

## Layout

```
backend/        Django + Strawberry GraphQL + Temporal (control plane)
frontend/      Next.js + React 19 + Apollo + Tailwind (web UI)
docker/        docker-compose + entrypoint + runtime config
docs/          Engineering docs (rendered separately by astrolift-docs)
schema.graphql GraphQL contract — written by backend, consumed by frontend
Makefile       Top-level orchestration wrappers
run.sh         Dev command center (./run.sh help)
```

## License

MIT — Copyright (c) 2026 Calliope Labs Inc. All Rights Reserved. Calliope AI is a trademark of Calliope Labs Inc.
