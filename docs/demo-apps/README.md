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

## Expanded set (#980) — monorepo, FaaS, managed-service combos

Validated live on the SteadyMD test bed (spec 41 Phase 4):

| Sample | Topology / coverage tested |
|--------|----------------------------|
| `calliopeai/astrolift-sample-monorepo` (repo: `apps/web` + `apps/api`) | Monorepo / multi-service — one repo, N apps discovered via `scanAppManifests`, each built from its own subdir `build_context` (#979) |
| `calliopeai/astrolift-sample-faas` (Dockerfile `FROM` an AWS Lambda base) | Provider-managed FaaS — `kind=faas`, container-image packaging, invoke via API Gateway HTTP API (#987) |
| `astrolift-sample-api` (image `calliopeai/astrolift-sample-api`) | The binding-reporting app used to exercise managed-service combos — it self-reports which bindings (`postgres`/`redis`/`queue`/`object_store`) reached the pod |

**Managed-service coverage** (provision → bind → four-corner teardown, all proven live; #982):
`postgres`(RDS) · `object_store`(S3) · `kv_store`(DynamoDB) · `redis`(ElastiCache) · `search`(OpenSearch) · `queue`(SQS) · `model_endpoint`(Bedrock) · `email`(SES). Declare them in a manifest's `[[managed_services]]` blocks or provision ad-hoc via `provisionManagedService`. The `astrolift-sample-api` image consuming `postgres` + `redis` + `queue` exercises the full RDS+cache+queue combo.

## Usage

Register any of these apps by pointing Astrolift at a repo that
contains the corresponding `astrolift.toml` at its root, or paste the
manifest contents directly in the manifest editor during app
registration.

For GA validation testing (spec #16), use the `multi-workload` config
on at least two different cloud providers to exercise the cross-cloud
deployment path.
