# Cognito client cleanup and the live smoke

Two one-command operator tools for steps that need a live install and a person's
own credentials. Nothing here runs in CI.

- `backend/scripts/ops/cognito_client.py` inspects and trims Cognito app clients
  (#2055, #1727).
- `npm run smoke:live` is a read-only browser smoke of a signed-in console
  (#1815, #2271).

## Cognito client tool

The tool needs boto3 only, and no Django. Run it from the repository root with
your AWS SSO profile; the region defaults to the pool ID's prefix.

```sh
aws sso login --profile conflict-calliope
python backend/scripts/ops/cognito_client.py --profile conflict-calliope <command> ...
```

| Command | What it does |
|---|---|
| `show --pool P --client C` | Prints the client's config. The client secret is redacted. |
| `clients --pool P` | Lists every client with its callback and logout URL counts, and whether it has a managed login branding (the pool has 20). |
| `trim --pool P --client C --keep-callback URL... --keep-logout URL...` | Prints the before and after CallbackURLs and LogoutURLs. Only `--apply` writes. |
| `retire --pool P --client C` | Deletes the client with `--apply`. Refuses while the client has any callback URL. |

`UpdateUserPoolClient` resets every field the request leaves out. `trim --apply`
sends back every writable field that `DescribeUserPoolClient` returned. The
writable set comes from botocore's UpdateUserPoolClient model, and only the two
lists change. The tool refuses, and writes nothing, when:

- Describe returns a field this botocore cannot send back. That field is newer
  than the installed botocore. Upgrade boto3 and run the command again.
- a `--keep-*` URL is not already on the client. Trim only removes URLs.
- the trim would drop the client's `DefaultRedirectURI`.

After the update it reads the client again. It exits 1 and names the fields if
anything other than the two lists changed. A refusal exits 2.

`--keep-callback` and `--keep-logout` are both required. Pass a flag with no URL
after it to empty that list.

### CONFLICT: trim the old ALB client (#2055)

Every CONFLICT app now signs in through the central client `1unkru25uqbon23mnhj2cpv3hd`.
The old client `1e3f2egf7ibju4c9rnldk98cvv` (`astrolift-conflict`, pool
`us-west-2_xU96Y7DAg`) still lists 12 per-app `/oauth2/idpresponse` callbacks.
Only the console's own callback is still used.

```sh
tool() { python backend/scripts/ops/cognito_client.py --profile conflict-calliope "$@"; }
POOL=us-west-2_xU96Y7DAg
CLIENT=1e3f2egf7ibju4c9rnldk98cvv

# 1. Read the current lists; copy the console's logout URL from LogoutURLs.
tool show --pool $POOL --client $CLIENT

# 2. Dry run: check the "-" lines are only the per-app hosts.
tool trim --pool $POOL --client $CLIENT \
  --keep-callback https://astrolift.us-west-2.conflict.softinfra.net/app/auth1/callback \
  --keep-logout <the console logout URL from step 1>

# 3. The same command with --apply.
```

Then sign in to `https://astrolift.us-west-2.conflict.softinfra.net/` to check the
console login still works.

### CONFLICT: turn on edge sign-out (#1727 item 1)

Envoy sign-out ends the Cognito session once the cluster's
`oidc_auth_config.logout_url` is set (#2391). The value for `conflict-astrolift`
uses the central client, and its `logout_uri` is already registered on it:

```text
https://conflict.auth.us-west-2.amazoncognito.com/logout?client_id=1unkru25uqbon23mnhj2cpv3hd&logout_uri=https%3A%2F%2Fauth.astro.conflict.cloud%2F
```

`updateTenantCluster` replaces `oidcAuthConfig` with what it is sent, but keeps
any secret the input leaves out. Send back the readable view, drop its `*_set`
flags and add `logout_url`. Use `--scope clusters`; the CLI has no `admin` scope.

```sh
astro auth login --scope clusters
astro api graphql --org conflict \
  -q 'query { astroliftCluster(slug: "conflict-astrolift") { id ingressClass oidcAuthConfig } }' \
  > cluster.json

jq --arg url 'https://conflict.auth.us-west-2.amazoncognito.com/logout?client_id=1unkru25uqbon23mnhj2cpv3hd&logout_uri=https%3A%2F%2Fauth.astro.conflict.cloud%2F' \
  '{input: {id: .astroliftCluster.id,
            oidcAuthConfig: ((.astroliftCluster.oidcAuthConfig
              | with_entries(select(.key | endswith("_set") | not))) + {logout_url: $url})}}' \
  cluster.json > update.json
cat update.json   # discovery_url, client_id, auth_proxy_host and logout_url; no secrets

astro api graphql --org conflict --vars-file update.json -q \
  'mutation($input: UpdateTenantClusterInput!) { updateTenantCluster(input: $input) { ok errors { message } data { oidcAuthConfig } } }'
```

The result's `oidcAuthConfig` should show `logout_url` and every `*_set` flag
still `true`. Then re-apply the edge policies:

```sh
astro api graphql --org conflict --var clusterId=<id from cluster.json> -q \
  'mutation($clusterId: GUID!) { reconcileClusterIngresses(input: {clusterId: $clusterId}) { ok errors { message } data { reconciledCount skippedCount errors } } }'
```

Check in a browser: open `https://conflict-quake-dash.astro.conflict.cloud/oauth2/logout`.
It should land on the auth host's "You have signed out" page, and the next visit
should ask you to sign in.

### SteadyMD: retire the tenant ALB client (#1727 item 3)

Run these as yourself under the SteadyMD identity and profile. The agent never
does. `astrolift-prd-tenant-alb-client` is the unbranded client that serves a hard
403 at login.

```sh
tool() { python backend/scripts/ops/cognito_client.py --profile <steadymd profile> "$@"; }
POOL=us-west-2_f2ddJYI7K   # check: the pool that holds astrolift-central-auth

# 1. Find the client ID. Check that no app still uses it: every live app should
#    redirect to the central client at login.
tool clients --pool $POOL

# 2. Empty both lists (dry run, then --apply).
tool trim --pool $POOL --client <tenant ALB client id> --keep-callback --keep-logout

# 3. Delete it (dry run, then --apply).
tool retire --pool $POOL --client <tenant ALB client id>
```

Cognito may reject an empty callback list while OAuth flows are on. The update
then fails and nothing changes. In that case, delete the client once step 1
confirms no app uses it. Deleting it frees nothing in the branding quota unless
the client has a branding; `clients` shows which ones do.

## Live smoke

A read-only signed-in pass over the console, for the UI steps of #1815 and the
metrics check of #2271. It runs headless Chromium against `LIVE_BASE_URL` with the
session saved in `LIVE_STORAGE_STATE`. That file holds your session cookies:
keep it outside the repository (the config refuses a path inside it) and delete
it afterwards.

Every GraphQL mutation and every non-GET call is aborted before it leaves the
browser, and the test fails if one was attempted. Two same-origin session
routes (`/api/session/getToken`, `/api/auth/store-token`) are allowed, because
they move the browser's own token and write nothing on the server. Telemetry
posts are dropped and listed. Any WebSocket other than the GraphQL one (the
exec terminal is a shell) is refused. Traces and videos are off.

```sh
cd frontend
export LIVE_BASE_URL=https://astrolift.us-west-2.conflict.softinfra.net
export LIVE_STORAGE_STATE=~/.astrolift-live/conflict.json

npm run smoke:live:login   # headed: sign in; saved once the console is back on /fleet
npm run smoke:live         # headless smoke
rm "$LIVE_STORAGE_STATE"
```

`LIVE_METRICS_APP` (default `conflict-quake-dash`) and `LIVE_METRICS_ENV`
(default `production`) pick the app for the metrics check.

The run writes `frontend/test-results/live/report.json` and screenshots under
`frontend/test-results/live/artifacts/`, both gitignored. Cookie values, tokens
and secret-bearing query values are redacted from the report.

| Test | Pass means | Fail means |
|---|---|---|
| fleet overview loads with no fixture rows | `/fleet` rendered its roster from GraphQL reads that all returned below 400 with no errors, and no route-walk fixture name appears. | `fixtureNames` lists mock rows on screen, or `failedGraphql` lists a read that failed (a 403 usually means the session expired: log in again). |
| agent overview shows runtime, activity and runs | The first roster agent's Overview shows Latest run, Runtime and Recent runs. | A section is missing or a read failed. Skipped when the fleet lists no agents. |
| overseer input is gated on a running run and the permission | The input is hidden when no listed run is RUNNING, and hidden when the session lacks `agent_task.send_input`. `overseer` records the statuses, the permission (`null` if the page did not read it) and whether the input showed. | The input was offered without a RUNNING run or without the permission. Nothing is sent either way. |
| production metrics show traffic and status-code series | `metrics.network` (the Traffic signal, HTTP requests/s) and `metrics.statusCodes` each have at least one series with samples. | Either reads `empty`, with the GraphQL HTTP status, error count and the panel's reason (for example `NOT_CONFIGURED`). Network RX/TX bytes are not in the GraphQL contract yet, so Traffic stands in for the network panel. |

Any test also fails when `blockedWrites` is not empty: the page tried to write.

### Local check against the route mocks

The specs run against the route-walk mocks, the same way
`playwright.routes.config.ts` does. Build with the mock API origin, start the
two servers yourself, and use a mock session:

```sh
cd frontend
NEXT_PUBLIC_API_ORIGIN=http://127.0.0.1:6172 API_INTERNAL_ROOT=http://127.0.0.1:6172 npm run build
node e2e-routes/api.mjs &
NEXT_PUBLIC_API_ORIGIN=http://127.0.0.1:6172 API_INTERNAL_ROOT=http://127.0.0.1:6172 npm run start -- --port 6171 &
cat > /tmp/live-mock-state.json <<'JSON'
{"cookies":[{"name":"sessionid","value":"owner","domain":"127.0.0.1","path":"/","expires":-1,"httpOnly":false,"secure":false,"sameSite":"Lax"},{"name":"backend_jwt","value":"route-fixture-token","domain":"127.0.0.1","path":"/","expires":-1,"httpOnly":false,"secure":false,"sameSite":"Lax"}],"origins":[]}
JSON
LIVE_BASE_URL=http://127.0.0.1:6171 LIVE_STORAGE_STATE=/tmp/live-mock-state.json LIVE_EXPECT_FIXTURES=1 \
  npx playwright test -c playwright.live.config.ts
```

`LIVE_EXPECT_FIXTURES=1` flips the fixture check for the mocks: it must find
fixture names there, which proves the detector works.
