# Local NVKE verification

The provider tests exercise all pinned HTTP operations over real HTTP/TLS.
The admission tests run against a separate, disposable Kubernetes API server
and etcd with the pinned NVKE CRDs. They verify resource persistence,
structural/CEL validation, immutable fields, resource-version conflicts,
UID-preconditioned deletion, pagination, namespace isolation and kubeconfig
Secrets. They do not run the NVKE controllers, provision machines or prove
GPU/node lifecycle behavior.

## Pin and inventory upstream

From the Astrolift app checkout:

```sh
NVKE_APP="$PWD"
NVKE_UPSTREAM=/tmp/astrolift-nvke-upstream
git clone https://github.com/dsx-ai-factory/nvke.git "$NVKE_UPSTREAM"
git -C "$NVKE_UPSTREAM" checkout 92c39b8ad563ebe5ef705f781b3038b901aba10f
mkdir -p "$NVKE_UPSTREAM/cli/cmd/astro-inventory"
cp backend/providers/scripts/nvke_cli_inventory.go "$NVKE_UPSTREAM/cli/cmd/astro-inventory/main.go"
cd "$NVKE_UPSTREAM"
go run ./cli/cmd/astro-inventory > /tmp/astrolift-nvke-cli-inventory.json
cd "$NVKE_APP/backend/providers"
python -m scripts.nvke_contract --upstream "$NVKE_UPSTREAM" \
  --cli-inventory /tmp/astrolift-nvke-cli-inventory.json --check
python -m scripts.nvke_coverage --check
```

The Go helper constructs the actual command tree without reading site
configuration or contacting an API. This includes commands missing from
upstream's generated reference documentation and preserves every command's
flags, types, defaults and aliases. Review all source/schema/command changes
before regenerating for a newer upstream revision.

## Start the isolated admission control plane

In a terminal, keeping the variables from above:

```sh
make -C "$NVKE_UPSTREAM/management-engine" setup-envtest \
  LOCALBIN=/tmp/astrolift-nvke-envtest ENVTEST_K8S_VERSION=1.37.0
NVKE_TEST_ASSETS=$(/tmp/astrolift-nvke-envtest/setup-envtest use 1.37.0 \
  --bin-dir /tmp/astrolift-nvke-envtest -p path)
mkdir -p "$NVKE_UPSTREAM/management-engine/cmd/astro-envtest"
cp "$NVKE_APP/verification/nvke/control-plane.go" \
  "$NVKE_UPSTREAM/management-engine/cmd/astro-envtest/main.go"
cd "$NVKE_UPSTREAM"
go run ./management-engine/cmd/astro-envtest "$NVKE_TEST_ASSETS" \
  "$NVKE_UPSTREAM/management-engine/config/crd/bases" \
  /tmp/astrolift-nvke-envtest.kubeconfig
```

The helper writes a mode-0600 kubeconfig with the context `astro-nvke-local`.
It leaves the user's default kubeconfig and contexts untouched. Stop it with
Ctrl-C after testing; it shuts down the local API server and etcd. Foreground
deletions remain pending in this environment because it has no garbage
collector, which the tests check explicitly.

## Run the provider checks

In a second terminal, from `backend/providers`:

```sh
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e '.[nvke,dev]' types-PyYAML
.venv/bin/ruff check nvke tests/nvke scripts/nvke_contract.py scripts/nvke_coverage.py
.venv/bin/ruff format --check nvke tests/nvke scripts/nvke_contract.py scripts/nvke_coverage.py
.venv/bin/mypy nvke --strict --follow-imports=silent
NVKE_TEST_KUBECONFIG=/tmp/astrolift-nvke-envtest.kubeconfig \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest \
  -p pytest_asyncio.plugin -c pyproject.toml --confcutdir=. \
  -o asyncio_default_fixture_loop_scope=function tests/nvke -q
.venv/bin/python -m build --wheel --no-isolation
```

The admission tests skip unless explicitly supplied the local kubeconfig.
They reject any other context name and any endpoint outside `127.0.0.1`.
No test uses an ambient Kubernetes context, cloud account or NVKE site.
Runtime JSON Schema checks validate structure; Kubernetes admission remains
authoritative for CEL rules and resource immutability.
