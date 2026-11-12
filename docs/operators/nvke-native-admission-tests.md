# Owned native NVKE admission tests

The provider and backend admission cases require a separate local Kubernetes API
server with the six pinned NVKE CRDs. They refuse an ambient kubeconfig, a context
other than `astro-nvke-local`, or an API hostname other than `127.0.0.1`.
This fixture runs API/etcd processes on macOS arm64 without creating a VM,
scheduler, controller manager or workload node. It does not qualify native
bootstrap, installation, enrollment, phase continuation or deployment.

Use a Python environment with the provider test dependencies and an explicit Go
toolchain supporting Kubernetes 1.36.4. Keep all commands in background jobs;
retain their process IDs, private logs, exit markers and JUnit reports.

## Build assets

From `backend/providers`, run the following in a background job with a new
absolute output directory whose parent already exists:

```sh
python tests/nvke/fixtures/build_owned_envtest.py \
  --opscode /absolute/path/to/reviewed/astrolift-opscode \
  --go /absolute/path/to/go \
  --output /absolute/path/to/new-private-admission-assets
```

The builder uses the selected Opscode 1.36.4 helper's module/replace configuration,
pins Kubernetes 1.36.4 and etcd 3.6.8 module checksums and upstream entrypoints,
verifies the resolved module graph, and builds with two Go workers and `-p=1`.
It checks actual binary versions and records binary/source/module hashes.
`build-report.json` and `build.exit` distinguish a completed successful build from
an incomplete or failed one. Failed output and step logs remain for diagnosis.
The selected checkout and existing assets are never modified.

## Start the isolated server

Require `exitCode: 0` in the exact build report before starting this background
job, again selecting a new absolute root:

```sh
python tests/nvke/fixtures/owned_envtest.py \
  --build-report /absolute/path/to/new-private-admission-assets/build-report.json \
  --root /absolute/path/to/new-private-admission-server
```

The server verifies asset hashes and entrypoints, creates private short-lived
credentials, and binds API and etcd listeners to loopback. It installs the full
six CRDs from this checkout's pinned `nvke/upstream/contract.json`, waits for each
CRD to be Established, and creates the backend fixture namespace `org-owned`.
Only then does it write `ready.json` and the private `kubeconfig.json`.
`ready.json` binds the contract revision/hash and exact asset build report hash.

Run tests from the same host so the loopback endpoint retains its meaning. Set
`NVKE_TEST_KUBECONFIG` to that private `kubeconfig.json`; never copy its contents
into logs or committed artifacts. The standalone provider cases are:

```sh
NVKE_TEST_KUBECONFIG=/absolute/path/to/new-private-admission-server/kubeconfig.json \
  python -m pytest -c pyproject.toml --confcutdir=. \
  tests/nvke/test_datastore_integration.py \
  tests/nvke/test_infrastructure_client_integration.py \
  --junitxml=/absolute/path/to/private-provider-admission.xml
```

Use the existing real PostgreSQL and Temporal test configuration for the backend
cases in `test_nvke_access.py`, `test_nvke_inventory.py`,
`test_nvke_operations.py`, `test_nvke_provider_observations.py` and
`test_nvke_resource_operations.py`. Use a dedicated test database and Temporal
namespace. Inspect completed JUnit results for failures, errors, skips and missing
cases; a successful asset build or server readiness marker is not a test result.

## Cleanup

The private root's `processes.json` records API/etcd child IDs. Preserve the
background launcher's PID as well. After tests finish, send SIGTERM to that exact
still-matching `owned_envtest.py` process. Its `finally` block terminates and waits
for its own children and writes `stopped.json`. Verify that marker and the owned
process handles before removing private test output. The server also shuts down
after its configured lifetime, two hours by default. It never stops another
cluster, changes kubeconfig defaults, or deletes a VM or retained native run.
