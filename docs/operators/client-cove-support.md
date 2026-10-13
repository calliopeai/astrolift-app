# Support module

The support module lets members of an organization open a support case with
Calliope AI for this install, and follow and answer it, from **Support** in the
account menu. Cases live in Client Cove. Astrolift forwards only what the person
types (summary, description, severity) plus the install's own deployment. It
sends no telemetry.

The module is off by default.

## Turn it on

Set these on the control plane, then switch it on:

| Setting | Value |
|---|---|
| `FEATURE_SUPPORT` | `true`. Without it the routes answer 404. |
| `CLIENT_COVE_SUPPORT_URL` | The Client Cove base URL, for example `https://clientcove.calliope.ai`. |
| `CLIENT_COVE_SUPPORT_API_KEY` | The shared support key Client Cove issued for this install. It stays on the server; the browser never receives it. |
| `CLIENT_COVE_DEPLOYMENT_ID` | This install's deployment ID in Client Cove. Every case is filed against it. The browser cannot choose another. |

Then turn on **Support** in the platform settings (`SUPPORT_ENABLED`). The
account menu shows Support only while it is on, and turning it off closes the
routes again without a redeploy.

## Who can use it

- Reading cases needs `org.read` in the active organization.
- An API token must belong to that organization and no team. Opening a case or
  replying with a token needs its `admin` scope.
- A browser session must send the UI header, which the web app always does.

Client Cove scopes every case to the organization the signed assertion names,
so a member never sees another organization's cases.

## What people see when it fails

| Situation | Message |
|---|---|
| The deployment has no active support coverage (Client Cove 402) | No active coverage; contact the account team. |
| `CLIENT_COVE_DEPLOYMENT_ID` or the key is missing, or Client Cove is unreachable | Support could not be reached. |
| The case belongs to another organization or was removed | This case is no longer available. |

Client Cove's own error text is never passed through to the browser.

## API

`GET` and `POST /api/support/v1/tickets/` list and open cases.
`GET` and `POST /api/support/v1/tickets/<id>/` read a case and add a reply.
Refusals carry a `code`: `PERMISSION_DENIED`, `SUPPORT_COVERAGE_REQUIRED`,
`SUPPORT_REQUEST_INVALID`, `NOT_FOUND` or `SUPPORT_UNAVAILABLE`.
