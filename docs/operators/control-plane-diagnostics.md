# Control-plane diagnostics and legacy uploads

`GET /app/metrics/` exposes the control-plane Prometheus registry only to an
active Django superuser (the platform operator). A staff user or organization
administrator does not qualify. Session authentication works; API-token
authentication additionally requires the `admin` scope. Configure Prometheus's
`authorization.credentials_file` with an operator's API token, store the file
outside the repository, and keep the endpoint behind the install's normal
network access controls. Anonymous scraping returns 403.

Preconditions: an active Django superuser (the platform operator). For
`GET /app/metrics/`, a browser session or an API token holding the `admin`
scope; for `confirmPreSignedUrlImageUpload`, a session or bearer in the active
organization (bearer writes also need the `admin` scope). Surfaces: the HTTP
route above and the GraphQL mutation. Placeholders: `<api-url>` is the
install's `PLATFORM_API_URL`; `<operator-api-token>` is an API token the
operator created at `/tokens`.

The old `/app/sentry-debug/` and `/app/test/open_telemetry/` routes are removed.
Use the configured Sentry and OpenTelemetry integrations for diagnostics.

`confirmPreSignedUrlImageUpload` permits an active uploader to update metadata
or soft-delete their own live upload. It scopes both `publicUrl` and `uploadId`
to `profile.organization()` and requires active, non-deleted legacy organization
membership. If both identifiers are present, they must address the same upload.
Only the platform operator may update another uploader's file, and the selected
organization still applies. An upload in a different organization is reported as
missing, including to the operator. Changing upload expiration uses the same
authorization check before updating any linked document.

Upload rows belong to the legacy `organization.Organization` model; API tokens
belong to `astrolift_identity.Organization`. Their integer primary keys are
independent. A generic upload bearer write requires `admin` and matching
organization GUIDs; it fails closed when the two identities do not match.
Session upload confirmation remains available for existing legacy identities.
There is no upload-specific API-token scope. Neither `read:apps` nor
`write:apps` authorizes this legacy write.

Upload initiation applies the same active identity and organization checks.
A caller-supplied upload UUID retries a live row only when that row belongs to
the same organization and uploader. It cannot transfer another user's upload,
move one between organizations, or revive a soft-deleted row, including for a
platform operator. Updating an existing profile-image upload uses this same
owner restriction.

Verification: `core/tests/test_access_holes_2174.py` executes GraphQL mutations
and HTTP routes against real PostgreSQL, checking both successful operations
and refusals without metadata, deletion or ownership changes.

## Dashboard query failures

An empty collection or a not-found object is shown only after its request
succeeds. On an initial network or permission failure, the affected screen,
panel or picker shows the failure and a Retry button (#2142). Retry repeats the
read; it does not assume the request succeeded or change access permissions.
For combined views such as cloud providers and driver reference, a failed
cluster lookup cannot establish that no clusters are bound. Model deployment
waits for environment targets and cluster GPU capabilities before proceeding.

Background refreshes retain successfully loaded content. A failed poll does
not close a running VNC session, discard a workflow or agent run, or replace
cached skill rows with an error frame. The displayed data remains the last
successful response until a later read succeeds; it is not evidence that the
latest refresh succeeded. Event rate keeps its frame visible for loading,
initial failure and a successful zero-event window.

Verification: the frontend `query-states.test.tsx` uses real Apollo clients and
React hooks to exercise transport refusals, retries and cached refreshes.
`query-frames.test.tsx` checks that the corresponding rendered screens show the
failure, preserve their frame and wire the retry action.

## Verify

```sh
curl -s -o /dev/null -w '%{http_code}\n' <api-url>/app/metrics/
curl -s -o /dev/null -w '%{http_code}\n' -H 'Authorization: Bearer <operator-api-token>' <api-url>/app/metrics/
./run.sh test core/tests/test_access_holes_2174.py
cd frontend && npx vitest run components/screens/query-states.test.tsx components/screens/query-frames.test.tsx
```

The anonymous request prints `403`, the operator request prints `200`, and both test runs pass.
