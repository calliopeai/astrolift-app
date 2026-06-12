# Demo sample apps

This directory contains example `astrolift.toml` manifests for common
application types and deployment topologies. Use these as starting
points when registering apps or testing a new cluster.

Each subdirectory contains:

- `astrolift.toml` — the manifest Astrolift reads.
- A note on what topology this config is designed to test.

## Catalog

| Directory | App type | Topology tested |
|-----------|---------|----------------|
| `nodejs-web/` | Node.js HTTP server | Single workload, auto TLS, environment inject |
| `python-fastapi/` | Python async API | Build from source, health probes, managed Postgres |
| `go-microservice/` | Go gRPC/HTTP service | Minimal image, resource limits, custom domain |
| `multi-workload/` | Web server + background worker | Multi-workload app, shared secrets, worker scaling |

## Usage

Register any of these apps by pointing Astrolift at a repo that
contains the corresponding `astrolift.toml` at its root, or paste the
manifest contents directly in the manifest editor during app
registration.

For GA validation testing (spec #16), use the `multi-workload` config
on at least two different cloud providers to exercise the cross-cloud
deployment path.
