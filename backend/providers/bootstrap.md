# Astrolift Providers -- Bootstrap

> **What this file is.** Conventions and layout for the providers tree at `astrolift-app/backend/providers/`. Read it before writing any code. Read the workspace `bootstrap.md` and `specs/02-multi-cloud-k8s-abstraction.md` first.

> **What this directory does.** Houses the **provider plugin SDK** (typed driver protocols) and the **per-cloud plugin implementations** that let Astrolift's control plane drive workloads on any Kubernetes cluster without cloud-specific code in the core.

> **History.** This tree used to live in a separate `astrolift-providers` repo that was vendored into `backend/vendor/astrolift-providers/`. As of the consolidation, the standalone repo was subtree-merged here (full history preserved) and the upstream repo was archived. All future work happens here.

---

## 1. Repo Layout

```
astrolift-providers/
  _sdk/                     # The provider SDK -- typed Protocol classes
    base.py                 # ProviderPlugin dataclass, DriverRegistry type
    cluster.py              # ClusterDriver protocol
    ingress.py              # IngressDriver protocol
    dns.py                  # DnsDriver protocol
    tls.py                  # TlsDriver protocol
    secrets.py              # SecretsBackend protocol
    identity.py             # WorkloadIdentityDriver protocol
    registry.py             # ImageRegistryDriver protocol
    object_store.py         # ObjectStoreDriver protocol
    managed_service.py      # ManagedServiceDriver protocol
    log_stream.py           # LogStreamDriver protocol
    metrics.py              # MetricsDriver protocol
  aws/                      # AWS plugin (EKS, ALB, Route53, ACM, etc.)
    plugin.py               # PLUGIN manifest + driver registrations
  gcp/                      # GCP plugin (GKE, Cloud DNS, etc.)
    plugin.py
  azure/                    # Azure plugin (AKS, Azure DNS, etc.)
    plugin.py
  k8s_native/               # Vanilla Kubernetes plugin (kind, k3s, etc.)
    plugin.py
```

---

## 2. The SDK / Plugin Split

### `_sdk/` -- The contracts

The `_sdk/` package defines `typing.Protocol` classes for every driver in the driver catalog. These are the **interfaces** that plugin authors implement. They have no runtime dependencies beyond the standard library and define the method signatures, type hints, and semantic contracts.

The SDK never imports from any plugin package. The dependency arrow is one-way: plugins depend on the SDK, never the reverse.

### Plugin packages -- The implementations

Each cloud directory (`aws/`, `gcp/`, `azure/`, `k8s_native/`) is a provider plugin. A plugin:

1. Implements some subset of the driver protocols from `_sdk/`.
2. Exports a `PLUGIN` constant (a `ProviderPlugin` instance) from `plugin.py` that declares the plugin id, display name, driver map, and config schema.
3. Registers as an entry point under `astrolift.providers` (see `pyproject.toml`).

Not every plugin implements every driver. A vanilla `k8s_native` plugin only covers `ClusterDriver`, `IngressDriver`, `LogStreamDriver`, and `WorkloadIdentityDriver`. Managed services, DNS, TLS, and others are composed from separate plugins or external controllers.

---

## 3. How to Add a New Provider Plugin

1. Create a new directory at the repo root (e.g. `digitalocean/`).
2. Add `__init__.py` and `plugin.py`.
3. In `plugin.py`, import the protocols from `_sdk` and create concrete classes that implement them.
4. Instantiate a `ProviderPlugin` with the implemented drivers and export it as `PLUGIN`.
5. Register the entry point in `pyproject.toml` under `[project.entry-points."astrolift.providers"]`.
6. Add a `mypy` override in `pyproject.toml` if the plugin depends on a cloud SDK with missing type stubs.

A plugin does not need to implement every driver. Only implement what the target cloud offers. The control plane validates at cluster registration time that a cluster's required capabilities are covered by its plugin.

---

## 4. Conventions

### Types

- Driver interfaces are `typing.Protocol` classes with `...` bodies. No implementation in the SDK.
- Use `dataclass` for data transfer objects (results, specs, statuses). Frozen where immutable.
- All public-facing IDs are `str` (UUID format). Never integer PKs.

### Code style

- Python 3.12+.
- `ruff format` + `ruff check` for formatting and linting.
- `mypy --strict` for type checking.
- Comments explain **why**, not what.

### Testing

- SDK protocols have no tests (they are pure interfaces).
- Each plugin provides unit tests against fakes and integration tests against real (or emulated) provider environments.
- Integration tests live in `<plugin>/tests/`.

### Naming

- Driver protocol classes: `<Thing>Driver` (e.g. `ClusterDriver`, `IngressDriver`).
- Plugin classes: `<Cloud>ProviderPlugin` (e.g. `AWSProviderPlugin`).
- Result dataclasses: `<Action>Result` (e.g. `ApplyResult`, `RolloutResult`).

### Result semantics

- An `ok=True` result means the backing resource now matches the spec. Never
  return it for work the driver did not do: the platform records it as applied.
  See [`docs/managed_service_updates.md`](docs/managed_service_updates.md) for
  the in-place update contract and how to derive `editable_fields()`.

---

## 5. Build and Verify

```bash
make fmt          # ruff format
make lint         # ruff check
make typecheck    # mypy strict
make test         # pytest
make verify       # all of the above
```

---

## 6. Entry Point Discovery

Plugins are discovered at control-plane boot via the `astrolift.providers` entry point group. Any installed package that registers under this group is loaded and its `PLUGIN` manifest is inspected. The control plane validates the manifest, checks driver protocol conformance, and registers the plugin for use by tenant clusters.
