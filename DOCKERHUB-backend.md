# Astrolift Backend

Control plane API for **[Astrolift](https://astrolift.app)** — the BYOC runtime layer for the Calliope AI ecosystem.

Django + GraphQL service that orchestrates tenants, workloads, and infrastructure across customer-owned cloud accounts (AWS, Azure, GCP, OCI). Backed by Postgres + Temporal.

## Quick Start

```bash
docker pull calliopeai/astrolift-backend:latest
```

### Run

```bash
docker run --rm \
  -e DJANGO_SETTINGS_MODULE=config.settings \
  -e POSTGRES_HOST=... -e POSTGRES_DB=... \
  -e POSTGRES_USER=... -e POSTGRES_PASSWORD=... \
  -e DJANGO_CACHE_URL=redis://... \
  -e TEMPORAL_ADDRESS=... \
  -p 8000:8000 \
  calliopeai/astrolift-backend:latest
```

Full deployment runs alongside the [`astrolift-frontend`](https://hub.docker.com/r/calliopeai/astrolift-frontend) image and is provisioned via [`astro install`](https://hub.docker.com/r/calliopeai/astrolift-cli).

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
