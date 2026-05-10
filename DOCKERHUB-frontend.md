# Astrolift Frontend

Operator UI for **[Astrolift](https://astrolift.app)** — the BYOC runtime layer for the Calliope AI ecosystem.

Next.js 14 (App Router) frontend that surfaces tenants, workloads, and infrastructure managed by the [`astrolift-backend`](https://hub.docker.com/r/calliopeai/astrolift-backend) control plane.

## Quick Start

```bash
docker pull calliopeai/astrolift-frontend:latest
```

### Run

```bash
docker run --rm \
  -e NEXT_PUBLIC_API_ORIGIN=https://api.your-astrolift-install.example \
  -p 3000:3000 \
  calliopeai/astrolift-frontend:latest
```

The image runs the standalone Next.js server (`node server.js`) on port `3000`.
Front it with the same load balancer that terminates traffic to `astrolift-backend` so the Django session cookie lands on the same origin.

## Tags

| Tag | Architecture | Description |
|-----|--------------|-------------|
| `latest` | multi-arch | Latest main build |
| `X.Y.Z` | multi-arch | Tagged release |
| `X.Y.Z-amd64` / `X.Y.Z-arm64` | single-arch | Per-architecture images |
| `main-<sha>` | multi-arch | Specific commit on main |

## Source

- Repo: [github.com/calliopeai/astrolift-app](https://github.com/calliopeai/astrolift-app)
- Project: [astrolift.app](https://astrolift.app)
- License: see repo

Part of the **Calliope AI** platform: [calliope.ai](https://calliope.ai)
