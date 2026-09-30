# Control-plane diagnostics and legacy uploads

`GET /app/metrics/` exposes the control-plane Prometheus registry only to an
active Django superuser (the platform operator). A staff user or organization
administrator does not qualify. Session authentication works; API-token
authentication additionally requires the `admin` scope. Configure Prometheus's
`authorization.credentials_file` with an operator's API token, store the file
outside the repository, and keep the endpoint behind the install's normal
network access controls. Anonymous scraping returns 403.

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
