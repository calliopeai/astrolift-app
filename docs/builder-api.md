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
404. A browser session works for a user with exactly one organization.

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
team only reaches them through a token issued for that team. Promote is
checked on its target team, so a team-level grant promotes into that team
and no other.

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
  are relative, without `..`. At most 100 files and 512 KiB in total,
  counted after decoding. Paths must be flat names for now; a path with a
  directory does not apply (#1873).
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
`409` for an environment that is not `running` or `failed`, has no
cluster, or sits on another organization's cluster.

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
- **Where the data file is stored.** It is stored in Postgres on the dev
  environment. On the cluster it is split into Secrets named
  `builder-data-part-NNNN` of at most 900 KiB each (about 73 for a 64 MiB
  file), once for the dev environment and once for the promoted app.
  Lower the Constance cap if etcd size matters on your clusters.
- **Hostnames.** Dev environments serve on `dev-<id without dashes>.<base>`
  and promoted apps on `<org-slug>-<app-slug>.<base>`, the app's namespace
  name (hash-shortened past 63 characters). `<base>` is
  `BUILDER_BASE_DOMAIN`, or `<org-slug>.dev.astrolift.io` when that is
  unset.
