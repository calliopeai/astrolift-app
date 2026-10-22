# Agent toolchain images

An environment spec's `imageTag` pins the image its agent task and box pods run.
A builder that needs heavy toolchains (Rust with the `wasm32` target, wrangler,
exact release binaries used as conformance oracles) should get them from that
image, built once, instead of installing them in every task. This page covers
building such an image, pinning it on a spec, pulling it, and what its size costs
on the cluster.

## Why not install at boot

With `allow_install = true` in its manifest's `[environment]`, an agent installs
its roster at the start of every task: `tool_preset` names `[roster.<group>]`
groups, and the runner `apt-get install`s their packages as root. A non-root spec
cannot install at all. `[workspace.deps]` installs Python and Node projects only.
Neither reaches rustup toolchains, compile targets or binaries from a release page,
and whatever they do install, every task downloads again. An image carries the
toolchain at the version you pinned, and a node that already holds the image starts
the next task from it.

## Build on a published agent image

The control plane does not inspect an image before it runs it. The runner, the
harness CLI and the `agent` user must already be inside, as the agent image
contract lists; every published agent image carries that contract as the
`astrolift-agent-contract` script. Add a thin layer to a published runtime image,
`docker.io/calliopeai/astrolift-agent-<runtime>`, pinned to its `main-<commit>` tag
or a digest; `latest` moves with every published build, and a rebuild of the same
commit pushes its `main-<commit>` tag again, so only a digest names one exact image.

```dockerfile
# A toolchain layer on a published agent runtime image.
ARG AGENT_IMAGE=docker.io/calliopeai/astrolift-agent-claude-code:main-cb9f9f0
FROM ${AGENT_IMAGE}

ARG RUSTUP_VERSION=1.29.2
ARG RUSTUP_SHA256=5be191e8c70a5007074254cb74d78dbfdd25cd5a61c27de7b8e5410122ec24f4
ARG RUST_VERSION=1.99.1
ARG WASM_BINDGEN_VERSION=0.2.129
ARG WASM_BINDGEN_SHA256=82d12bb940e2d4e72e0d5605387fc1b8ca179044e012b620f0ce4e7440e8320e
ARG WRANGLER_VERSION=4.151.3
ARG FOSSIL_VERSION=2.28
ARG FOSSIL_SHA256=c9d2bf9d150dff1076eae1b0b822ed3cde892e1273f78b040c4f8ace1b59dd40

# Build scripts and proc macros compile for the host, so cargo needs a C
# linker even when every crate targets wasm32.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*

# Outside /root, so uid 42042 can run it in non-root mode. The toolchain
# stays root-owned; cargo's caches under CARGO_HOME are writable by the
# agent group. Login shells (boxes, tmux) reset PATH, hence profile.d.
ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH
RUN curl -fsSLo /tmp/rustup-init \
        "https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/x86_64-unknown-linux-gnu/rustup-init" \
    && echo "${RUSTUP_SHA256}  /tmp/rustup-init" | sha256sum -c - \
    && chmod +x /tmp/rustup-init \
    && /tmp/rustup-init -y --no-modify-path --profile minimal \
        --default-toolchain "${RUST_VERSION}" --target wasm32-unknown-unknown \
    && rm /tmp/rustup-init \
    && chgrp -R agent "$CARGO_HOME" && chmod -R g+w "$CARGO_HOME" \
    && echo 'export PATH="/usr/local/cargo/bin:$PATH"' > /etc/profile.d/rust.sh

# wasm-bindgen must match the wasm-bindgen crate version in Cargo.lock.
RUN curl -fsSLo /tmp/wasm-bindgen.tar.gz \
        "https://github.com/wasm-bindgen/wasm-bindgen/releases/download/${WASM_BINDGEN_VERSION}/wasm-bindgen-${WASM_BINDGEN_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
    && echo "${WASM_BINDGEN_SHA256}  /tmp/wasm-bindgen.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/wasm-bindgen.tar.gz --no-same-owner --strip-components=1 -C /usr/local/bin \
        "wasm-bindgen-${WASM_BINDGEN_VERSION}-x86_64-unknown-linux-musl/wasm-bindgen" \
    && rm /tmp/wasm-bindgen.tar.gz

ENV WRANGLER_SEND_METRICS=false
RUN npm install -g "wrangler@${WRANGLER_VERSION}" && npm cache clean --force

# The conformance oracle: one exact release, checked before it is trusted.
RUN curl -fsSLo /tmp/fossil.tar.gz "https://fossil-scm.org/home/uv/fossil-linux-x64-${FOSSIL_VERSION}.tar.gz" \
    && echo "${FOSSIL_SHA256}  /tmp/fossil.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/fossil.tar.gz --no-same-owner -C /usr/local/bin fossil \
    && rm /tmp/fossil.tar.gz

# Inherited labels say "platform"; this image is not the platform's build.
LABEL ai.astrolift.agent-image.lineage="acme-factory"
```

The versions are the current ones on the day it was built; pin the ones your
repositories need. What each part is for:

- **Outside `/root`.** A non-root spec runs the pod as uid/gid 42042 with every
  capability dropped, and a tool under `/root` is out of its reach. rustup installs
  into the building user's home unless `RUSTUP_HOME` and `CARGO_HOME` say otherwise.
  Cargo writes its registry and git caches under `CARGO_HOME`, so that directory is
  writable by the agent group.
- **A C linker.** The published images have none, and cargo links build scripts and
  proc macros for the host even when every crate targets `wasm32`.
- **`/etc/profile.d`.** Debian's `/etc/profile` resets `PATH` in every login shell,
  and boxes use them: the keep-alive starts with `sh -lc`, and tmux windows run the
  user's login shell. The profile entry puts cargo's directory back.
- **The repository's toolchain.** A `rust-toolchain.toml` naming another version
  makes rustup download it at the first cargo call of every task, and in a non-root
  task that call fails, because `RUSTUP_HOME` is not writable for it. Bake the version
  and targets each repository pins.
- **Exact binaries.** The wasm-bindgen CLI must match the `wasm-bindgen` version in
  `Cargo.lock`. An oracle binary comes from one release and is checked against its
  sha256 before the build trusts it.
- **Crates.** Every pod starts with `CARGO_HOME` as the image left it, so every task
  downloads the crates it builds. Vendor them in the repository (`cargo vendor`), or
  run `cargo fetch` against the repository's `Cargo.lock` in the image build; a later
  lockfile change then downloads only the difference.
- **Lineage.** The base's labels say `lineage="platform"`. Set your own, so
  `docker inspect` on a running image says who built it.

Check the build with the contract's runtime tier, which the image inherits from its
base, as the image's default user and again the way non-root mode runs it:

```sh
docker run --rm --entrypoint astrolift-agent-contract <image> runtime
docker run --rm --user 42042:42042 --cap-drop ALL --security-opt no-new-privileges \
    --entrypoint astrolift-agent-contract <image> runtime
```

Then build one of your crates for `wasm32-unknown-unknown` with the same
`docker run` flags. A cache or toolchain directory the agent user cannot write then
fails on your machine rather than in a task.

### VNC specs

A task dispatched with a `vncEnabled` spec runs the `-vnc` repository of its image
(boxes run the image as it is): `registry/repo:tag` becomes `registry/repo-vnc:tag`.
Build the same layer on the runtime's `-vnc` image (`astrolift-agent-<runtime>-vnc`)
and push it there with the same tag. A digest is kept as it is: `repo@sha256:<d>`
becomes `repo-vnc@sha256:<d>`, and `repo:tag@sha256:<d>` becomes
`repo-vnc:tag@sha256:<d>`. A registry pulls either by the digest alone, so it resolves
only if the `-vnc` repository holds that very image. So pin a VNC spec by digest with
the `-vnc` image's own repository and digest; a repository that already ends in `-vnc`
is used unchanged.

## Pin it on the spec

An agent registered from a repository owns the spec that shares its slug. Every
manifest sync writes that spec's `imageTag` from the `image_ref` of the agent's
primary container, together with the manifest's `[environment]` values, `[secrets]`
refs, `tool_preset` and `allow_install`; an imported Agent Package writes its image,
environment values and secret refs at each import. Put the image in the manifest:

```toml
[[workloads]]
name = "builder"
kind = "agent"

  [[workloads.containers]]
  name = "agent"
  is_primary = true
  image_ref = "registry.example.com/factory/agent-toolchain:rust-1.99.1"
```

An `imageTag` set through the API on that spec lasts until the next sync. The sync
leaves `runtime`, VNC, managed model, non-root, box workspace, model gateway and GPU
settings as they are.

Any other spec takes the image from `imageTag` on `createAgentEnvironmentSpec` or
`updateAgentEnvironmentSpec`, `astro agent env-spec upsert <slug> --image-tag <ref>`,
or `manage.py upsert_agent_environment_spec --image-tag <ref>`. The reference is used
as written, up to 512 characters; nothing checks it before a task pulls it.

- **Precedence.** `imageTag` wins over `runtime`, which wins over the agent's primary
  container image. Box pods resolve their image the same way.
- **Keep `runtime` set** to the runtime the image derives from. It no longer picks
  the image, but the model gateway wiring, conversation transfer and the fleet list
  still read it.
- **Which spec a task uses.** The one its dispatch names (`environmentSpecId`), else
  the organization's spec whose slug is the agent's slug, when the agent's app may
  use it.
- **Credentials the tools use**, such as wrangler's `CLOUDFLARE_API_TOKEN`, go in
  `secretRefs`, never `envVars`: env vars are stored and shown in plain text.

The spawn reads the spec when it creates the Job. A new image, from a sync or an
update, therefore reaches every task that has not started; running pods keep their
image, and a box takes the new one at its next start. For a spec no manifest owns,
roll a new toolchain by updating the image alone:

```graphql
mutation {
  updateAgentEnvironmentSpec(
    slug: "rust-toolchain"
    input: { imageTag: "registry.example.com/factory/agent-toolchain:rust-1.99.1-r2" }
  ) {
    ok
    errors { code message field }
  }
}
```

`astro agent env-spec upsert` sends the image, runtime, tool preset, install and VNC
switches, config repository and branch, env vars and secret refs on every call, and
`upsert_agent_environment_spec` rewrites every field it takes. Pass all of them again
with either, or the spec loses the ones left out.

## Pulls and node caching

| `imageTag`                        | Task pod `imagePullPolicy` |
| --------------------------------- | -------------------------- |
| a version tag, `:rust-1.99.1`     | `IfNotPresent`             |
| a digest, `@sha256:...`           | `IfNotPresent`             |
| `:latest`, or no tag              | `Always`                   |

Box pods set no policy, and Kubernetes' default comes to the same thing. A catalog
`runtime` resolves to `:latest` unless the install sets `ASTROLIFT_AGENT_RUNTIME_TAG`,
so those tasks check the registry at every start.

With `IfNotPresent`, the first task on a node pulls the image, and later tasks there
start from the node's copy. A node pulls it again after the kubelet's image garbage
collection removes it (by default once disk use passes 85%, until it is back under
80%), and every new node pulls it once: an autoscaler scale-up, a replaced Spot node,
a Karpenter consolidation or its 720-hour node expiry. A DaemonSet running the image
on every node takes that first pull off the first task.

The pull is part of the task's time: the Job's `activeDeadlineSeconds`, the task
timeout, counts from when the Job is created. A pull that fails ends the task with
`container never started: ImagePullBackOff` (`InvalidImageName` for a malformed
reference), and the Job is deleted.

Measured on 2026-10-22 for the example above (download is the compressed size a node
pulls, unpacked the files on disk):

| Part                                       | Download | Unpacked |
| ------------------------------------------ | -------- | -------- |
| `astrolift-agent-claude-code:main-cb9f9f0` | 427 MB   | 1.11 GB  |
| Rust 1.99.1 with the `wasm32` target       | 257 MB   | 0.75 GB  |
| wrangler 4.151.3                           | 64 MB    | 0.21 GB  |
| gcc, libc6-dev, wasm-bindgen, fossil       | 74 MB    | 0.13 GB  |
| Whole image                                | 822 MB   | 2.20 GB  |

## Registry credentials

The task pod sets no `imagePullSecrets` and runs as the agent namespace's `default`
ServiceAccount, or as `astrolift-agent-model` for a managed-model spec. Box pods do
the same. A private registry is reachable in one of two ways.

- **Node credentials.** EKS: the node IAM role's ECR pull policy covers the cluster's
  own account, and a repository in another account must also allow that role in its
  repository policy. GKE: the node service account needs Artifact Registry Reader on
  the repository. AKS: the kubelet identity needs `AcrPull` on the registry.
- **A pull secret on the ServiceAccount.** Kubernetes adds a ServiceAccount's
  `imagePullSecrets` to every pod that names none. The agent namespace is
  `astrolift-agents-<org slug>` (a name over 63 characters is cut and given a hash
  suffix; `kubectl get ns -l astrolift.io/component=agents` lists them), and the
  organization's first agent task or box creates it. The platform never writes
  `imagePullSecrets`: it does not touch `default`, and it applies
  `astrolift-agent-model` with server-side apply, which keeps fields it does not set.
  You may also create `astrolift-agent-model` yourself before the first
  managed-model task; the platform then adds its labels and annotation to it.

```sh
NS=astrolift-agents-<org slug>
kubectl -n "$NS" create secret docker-registry agent-images \
    --docker-server=registry.example.com --docker-username=<user> --docker-password=<token>
kubectl -n "$NS" patch serviceaccount default \
    -p '{"imagePullSecrets":[{"name":"agent-images"}]}'
```

## Disk and memory

From the pod as the platform renders it, and the installer's node configuration:

- **CPU and memory.** An agent task or box container requests 250m CPU and 512Mi and
  is limited to 4 CPUs and 8Gi, install-wide, through `AGENT_POD_CPU_REQUEST`,
  `AGENT_POD_CPU_LIMIT`, `AGENT_POD_MEMORY_REQUEST` and `AGENT_POD_MEMORY_LIMIT` on the
  backend and the worker. The `cpu_request` and `memory_request` in an agent's
  manifest do not change them. A link step past the memory limit is OOMKilled;
  `CARGO_BUILD_JOBS` in the spec's `envVars` caps cargo's parallel jobs, and with
  them its peak memory.
- **No ephemeral storage bounds.** The pod requests and limits no ephemeral storage.
  A task pod mounts no volume apart from a project filesystem attached to its spec,
  at that filesystem's mount path, so the checkout, `target/`, and the cargo and npm
  caches land in the container's writable layer on the node's root disk. A box's
  workspace is an `emptyDir` with no `sizeLimit`, on the same disk. The scheduler
  reserves no disk for any of it. When a node runs short (by default under 10% free,
  or 15% for images), the kubelet evicts first the pods using more than they
  requested, and an agent pod requests nothing.
- **Node disks.** The installer's EKS CPU node groups keep the AL2023 AMI's 20 GiB
  gp3 root volume, and its Karpenter CPU pools set 20 GiB (GPU pools 100 GiB). That
  one volume holds the OS, the images, every pod's writable layer and the logs. These
  nodes keep only an image's unpacked layers once a pull finishes (containerd's
  `discard_unpacked_layers`); the download needs room beside them while it runs. On
  any other cluster, read the root volume size of the node pools agents land on.
- **Agents pack densely.** Small requests let the scheduler place many agent pods on
  one node, and nothing counts their disk.

Managed IDE boxes (`runtime = "calliope-managed-ide"`) are the exception: they size
their own containers and keep their workspace on a volume claim.

Budget a node's root disk for the image (2.2 GB unpacked for the example, plus its
0.8 GB download while it pulls), and for each task that can run there at once, its
checkout and `target/`. On 20 GiB nodes, give the nodes that run agents larger root
volumes, keep `target/` small (`CARGO_INCREMENTAL=0` and `CARGO_PROFILE_DEV_DEBUG=0`
in `envVars`), or both.

## Check a dispatched task

```sh
NS=astrolift-agents-<org slug>
JOB=agent-task-<task guid without dashes>
kubectl -n "$NS" get job "$JOB" \
    -o jsonpath='{.spec.template.spec.containers[0].image} {.spec.template.spec.containers[0].imagePullPolicy}{"\n"}'
kubectl -n "$NS" get events --field-selector involvedObject.kind=Pod | grep -E 'Pulled|Pulling'
```

`Pulled` with a duration is a cold pull; `Container image ... already present on
machine` is the node's copy.

Verified with real-PostgreSQL tests
(`backend/astrolift_dispatch/tests/test_env_spec_image_reaches_task_pod_2491.py`):
a spec created through the API, a task dispatched through `runAstroliftAgent`, and
the dispatch activity rendering its Job with the platform's spawner. They cover the
image and pull policy for a digest on a registry with a port, a version tag and
`:latest`; an image-only update reaching a task queued before it; and the `-vnc`
repository for each form. `test_agent_box.py` covers box pods, and
`test_agent_skill_resolution.py` and `test_team_isolation_1866.py` cover a manifest
sync writing and rewriting the spec's image. The example image was built for
linux/amd64 on 2026-10-22 and passed the agent image contract's runtime tier
(version 8) as root and as uid 42042 with every capability dropped. As that user it
built a `wasm32` crate depending on `wasm-bindgen` 0.2.129 from crates.io, generated
its bindings, ran wrangler and fossil, found cargo from a login shell, and could not
install the toolchain a mismatched `rust-toolchain.toml` asked for. The cluster side
was not exercised here: pull policy, node caching, garbage collection and eviction
are Kubernetes' documented behaviour, not measured on a cluster.
