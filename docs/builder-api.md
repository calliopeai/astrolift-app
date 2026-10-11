# Builder API

The App Builder REST surface: create a dev environment from a file tree,
sync new files into it, and promote it to a registered app that Astrolift
serves. Clients include the Calliope App Builder and Chat Studio's
"Ship to Astrolift".

All routes live under `/api/builder/v1/` and take and return JSON.

## Authentication

Send an API token as `Authorization: Bearer alft_at_...`. The CLI device
flow issues these, one organization per token. The token's organization
scopes every request: a dev environment in another organization answers
404.

Nothing else is accepted, a browser session included: a request without
an API token answers `401` with `"reason": "api_token_required"`. The
routes are CSRF-exempt and previews serve user code on the builder's base
domain, so a signed-in browser alone must not be enough to act.

The edge must let a bearer request through to `/api/builder/v1/*` without
an SSO challenge, the same bypass `/api/cli/v1/*` needs.

## Authorization

The organization must have the `chat_studio_integration` module on. After
that, the caller needs:

| Route | Permission |
|---|---|
| create, sync files | `app.create` in the organization |
| promote | `app.create` and `app.deploy` on the team the app lands in |

A token must also carry the `write:apps` scope (or `admin`). Scopes cap
what the token's user may do; they never add to it. CLI device-flow
tokens carry `write:apps`. Tokens from IDE, mobile or browser enrollment
are read-only and cannot use these routes.

Create and sync are checked at the organization level. A role granted on a
team only reaches them through a token issued for that team, and they
refuse a token whose team has since been deleted. Promote is checked on
its target team, so a team-level grant promotes into that team and no
other. Request headers such as `X-Astrolift-Team` change none of these
checks.

A refusal is `403` with a reason, in the same shape as the module check:

```json
{"detail": "the api token lacks the write:apps scope", "reason": "missing_scope", "scope": "write:apps"}
{"detail": "app.deploy is required on team 'eng'", "reason": "missing_permission", "permission": "app.deploy"}
{"detail": "the chat_studio_integration module is not enabled for this organization", "reason": "module_not_enabled", "module": "chat_studio_integration"}
```

## Create a dev environment

`POST /api/builder/v1/dev-environments/`

| Field | Default | Notes |
|---|---|---|
| `runtime` | `python` | `python`, `node`, `ruby`, `go` or `static` |
| `runtime_version` | image default | e.g. `3.12` for `python:3.12-slim` |
| `start_command` | none | run with `sh -c` in `/app` |
| `port` | `8080` | the app listens here; also set as `PORT` |
| `env_vars` | `{}` | name to value |
| `resource_profile` | `small` | `small`, `medium` or `large` |
| `cluster_guid` | the org's first managed cluster, else a shared one | one of the org's clusters or a shared one; any other answers `404` |

Returns `201` with `{"id", "status": "creating", "preview_url": null}`.
Provisioning runs in the background. Syncing files answers `409` until it
finishes.

## Sync files

`PUT /api/builder/v1/dev-environments/<id>/files/`

```json
{
  "files": {
    "server.py": "import http.server ...",
    "logo.png": {"content": "iVBORw0KGgo...", "encoding": "base64"}
  },
  "data_file": {"path": "data.sqlite", "content": "U1FMaXRl...", "encoding": "base64"}
}
```

- **`files`** replaces the whole tree. A value is UTF-8 text, or
  `{"content": <base64>, "encoding": "base64"}` for a binary file. Paths
  are relative, without `..`. Nested paths are supported. The operator sets `BUILDER_MAX_FILES`
  (default 10,000) and `BUILDER_FILES_MAX_BYTES` (default 64 MiB total,
  counted after decoding).
- **`data_file`** declares one data file, such as an app's SQLite
  database. It has its own cap, `BUILDER_DATA_FILE_MAX_BYTES` in Constance
  (default 64 MiB, decoded). Omit it to keep the stored data file; send
  `null` to remove it.
- **base64** is the standard alphabet with padding and no line breaks.

Returns `200` with `{"id", "status": "syncing", "file_count"}`. Errors:
`400` for a malformed or oversized entry, `404` for an unknown
environment, `409` when the environment is not `running` or sits on
another organization's cluster, and `413` when the body is larger than
the caps allow (checked before it is read).

## Promote

`POST /api/builder/v1/dev-environments/<id>/promote/`

| Field | Default | Notes |
|---|---|---|
| `app_name` | required | |
| `app_slug` | from `app_name` | DNS label, unique in the org |
| `team_slug` | the org's first team | the permission check runs on this team |
| `environment_name` | `production` | |
| `domain` | none | a managed domain zone bound to the environment: one of the org's zones or a shared one; any other answers `404` |

Returns `202`:

```json
{
  "id": "<dev environment id>",
  "status": "promoting",
  "app_guid": "<app guid>",
  "app_slug": "sales",
  "app_url": "https://acme-sales.acme.dev.astrolift.io",
  "data_persistent": false
}
```

Promote registers the app (`source_kind` `direct_upload`), onboards it,
and serves the promoted files from the app's own namespace at `app_url`.
The app keeps running independently of the dev environment. It answers
`409` for an environment that is not `running`, `failed`, or already
`promoting`, has no cluster, or sits on another organization's cluster.
The runtime is recorded as a Workload + Deployment on the app, so its
pages, rollback and observability see it the same way they see a
manifest-driven deploy's.

Promoting the same dev environment again with the same `app_slug` updates
that app in place instead of `409`ing: sync new files onto the dev
environment, then promote again to ship them. Onboarding does not repeat;
only the runtime re-renders. The destination team must still be the app's
own -- promote does not move an app between teams.

`data_persistent` answers "does data the app writes survive a restart":

- `true`: the data file sits on a persistent volume. Astrolift uses the
  cluster's default StorageClass (or its only one) and requires that
  class's CSI driver to be installed.
- `false`: the cluster cannot provision one (no StorageClass, a CSI class
  without its driver, or the cluster could not be asked). The data file
  sits on an emptyDir and resets to the shipped copy whenever the pod is
  replaced. Warn the user.
- `null`: no data file was declared.

## Where things land in the pod

| Path | Contents |
|---|---|
| `/app` | the files, read-only; the working directory |
| `/data/<data_file.path>` | the data file, writable |

The app reads `PORT`, plus `ASTROLIFT_DATA_DIR` (`/data`) and
`ASTROLIFT_DATA_FILE` (the data file's full path) when a data file is
declared. Open the database at `$ASTROLIFT_DATA_FILE`: SQLite writes its
journal beside the file, and `/app` is read-only.

An init container writes the data file into `/data` only when it is not
already there. A dev environment gets a fresh copy on every sync. A
promoted app on a persistent volume keeps what it wrote. A promoted app on
an emptyDir starts from the shipped copy each time its pod is replaced.

## Operator notes

- **Request size.** The data file travels base64-encoded, a third larger
  than the file. A 64 MiB data file makes a request of about 86 MiB, so an
  ingress in front of the control plane must allow that body size on
  `/api/builder/v1/*` (ingress-nginx defaults to 1 MiB). Other endpoints
  keep Django's 2.5 MiB limit.
- **Artifact storage.** Immutable ZIP archives live in the private `builder_artifacts`
  Django storage alias. The default is a scoped `BuilderArtifact` row in Postgres,
  shared by API and worker replicas. An operator can configure a private shared
  object-store backend under the same alias. Never point it at public media storage.
  Existing dev environment source fields remain compatible with older APIs.
- **Cluster transport.** The `fetch-artifact` init container uses an artifact-specific
  credential, mounted only into that container, to download the archive. It refuses
  redirects, checks the compressed length and SHA-256, checks expanded size and
  paths, then copies assets to `/app` and seeds an absent data file in `/data`.
  App/SQLite bytes never enter ConfigMaps or Secrets. Each revision has its own
  small credential Secret, so existing deployments keep their original artifact
  through later edits and restarts. Removing the preview revokes downloads unless its promoted app remains live;
  retained promoted apps can restart independently of preview teardown or removal.
- **API reachability.** Set `BUILDER_ARTIFACT_BASE_URL` to the control-plane API
  origin reachable from tenant clusters (defaults to `PLATFORM_API_URL`). Use HTTPS
  across networks; HTTP supports local development. No URL comes from the app.
  New pod starts need this endpoint and the private artifact store available.
- **Upgrade.** Apply migration `0046_builder_artifact`, deploy API and worker together,
  then re-ship existing apps to switch transport. Older clients remain compatible.
  Rolling back the control plane does not require dropping the additive table, but
  pods using the new transport still need the artifact endpoint available. Do not
  delete artifacts or revision credentials while a deployment may restart from them.
- **Hostnames.** Dev environments serve on `dev-<id without dashes>.<base>`
  and promoted apps on `<org-slug>-<app-slug>.<base>`, the app's namespace
  name (hash-shortened past 63 characters). `<base>` is
  `BUILDER_BASE_DOMAIN`, or `<org-slug>.dev.astrolift.io` when that is
  unset.

## Destination capabilities

`GET /api/builder/v1/capabilities/` requires the linked organization's API bearer
with `write:apps` and the enabled builder module. It returns `artifact_transport`
(`private-archive-v1`), `nested_paths`, `max_files`, `max_file_bytes` (total decoded
assets) and `max_data_bytes`. Studio checks these values before uploading; it does
not infer deployment capacity from the size of a Kubernetes configuration object.

The initial 512 KiB limit was deliberately for snippets that fit one ConfigMap.
Artifact transport removes that dependency. Capacity is now an operator resource
budget; it is not a framework, browser or Brain format limit. Data still travels
base64 on this compatible upload endpoint, so very large workloads may benefit
from a future streaming upload API.
