# Legacy core API access

The core GraphQL declarations remain available for compatibility (#2110).
Repository client usage includes `fileUpload`; the checked web operations do
not call the unavailable legacy organization, activation or sign-request
fields. No field or input was removed and the GraphQL contract is unchanged.

`effectivePermissions` and `permissionDiagnose` remain own-account reads.
Inspecting another person requires `org.manage_members` at the explicit active
organization and live target membership, or an active platform operator with
an `admin` bearer. `permissionCompare` uses the same organization-management
gate for both people. Selected team/project headers and grants under those
scopes do not authorize organization management. A manager's bearer must
belong to that organization and carry no narrower team boundary.

The global legacy mutation audit, Django group/profile management, library
writes, generic deletion and legacy member-status changes require the active
platform operator. API bearers additionally require `admin`; organization
administrators and staff sessions do not qualify. Generic deletion keeps its
existing seven-model allowlist.

Own-account PIN, notification and Rocket.Chat token actions require an active
account. Their generic bearer writes require `admin`. Browser sessions keep
the existing own-account behavior. Impersonation uses browser sessions only,
requires active accounts and keeps the existing shared switch-group rule;
bearers cannot switch session identities. Returning to an inactive original
account is refused. Legacy self-deletion requires a currently elevated session
and applies the same locked last-owner floor as `astroliftAnonymizeUser`.
Successful deletion deactivates the user's Astrolift memberships as well as
anonymizing the account. `updateMyProfile` remains the normal profile editor;
the generic `profile` serializer stays operator-only.

The legacy organization directory uses `organization.Organization`, which is
distinct from Astrolift's organization model. It is limited to the caller's
live legacy memberships and live peer roster; removed memberships and deleted
organizations are omitted. Active operator sessions can inspect the install.
Legacy-directory bearers require `admin` and match organization GUIDs rather
than independent integer primary keys.

`fileUpload`, upload processing and profile-image mutations retain their
legacy organization/ownership checks. They refuse inactive accounts, missing
or stale legacy organization membership, and non-admin or foreign-GUID
bearers before creating a wrapper or reaching storage. Existing upload and
wrapper identifiers must be live in the caller's legacy organization;
personal wrappers and processes belong to the caller. Cross-organization
upload replacement remains refused for operators too. Upload confirmation
retains its existing URL/ID intersection and owner checks.
Generic presigned targets compare their actual organization's GUID and refuse
foreign or removed owners before storage, including for operators. Targets
from the Astrolift organization model additionally require the operator;
legacy integer IDs cannot authorize writes to a modern app.

`activate`, `organization` and `upsertOrganization` always refuse after the
operator check; their former implementations had no working activation hook
or registered organization form. Use typed Astrolift identity mutations.
Sign-request fields always refuse before resolving caller-supplied IDs.
`uploadTextFile` still requires the optional, absent domain application.

`GET /app/export/` declares and enforces platform-operator access before
consulting the install-wide exporter registry. There are no built-in exporters;
the removed Rocket.Chat history exporter stays unavailable. The feature-gated
`GET/POST /api/support/v1/tickets/` requires `org.read` at the explicit live
organization, with bearer organization/team ceilings, before calling ClientCove.
Creating one's own ticket through a bearer additionally requires `admin`.
The support client receives that organization row for its signed assertion.
