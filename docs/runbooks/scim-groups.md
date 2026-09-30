# SCIM Groups

Use the same `/api/scim/v2/` base URL and `alft_st_` org credential as SCIM Users.
`manage.py issue_scim_token --org <slug>` issues the credential; disabling SCIM or
rotating/revoking that credential also stops Group requests. The request's token
owns the organization; a body, route identifier or tenant header cannot select
another organization. The existing credential rate limit also covers Groups.

`/Groups` supports GET with `displayName`, `externalId` and `id` filters and
`startIndex`/`count` paging, and POST creation. `/Groups/<id>` supports GET, PUT,
PATCH and DELETE. Responses use the SCIM Group schema, public UUID identifiers,
member references to `/Users/<id>`, and resource metadata. Mutations return an
ETag; send it as `If-Match` to refuse overwriting a changed group.

Create a group with the provisioned User resource IDs:

```json
{
  "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
  "displayName": "Engineering",
  "externalId": "idp-engineering",
  "members": [{"value": "<SCIM User id>"}]
}
```

Configure a `GroupRoleMapping` or group `RoleBinding` in that organization using
the group's `externalId`. If the IdP omits `externalId`, use the server-issued
Group `id`. A display-name rename retains the mapping identity. Changing
`externalId` changes the mapping identity and retires the previous identifier so
stale SSO claims cannot restore its former grants. Nested groups are not supported;
membership references must identify active User memberships of this organization.

Membership PATCH accepts add, replace and remove on `members`, remove with a
`members[value eq "<User id>"]` path, and add/replace attribute objects without a
path. A remove on `members` without a value clears the membership list. Multiple
operations are atomic: an invalid reference or unsupported path saves none of
them. PUT replaces the name and members; an omitted members list clears it.

```json
{
  "Operations": [
    {"op": "add", "path": "members", "value": [{"value": "<User id>"}]},
    {"op": "remove", "path": "members[value eq \"<other User id>\"]"}
  ]
}
```

Group-derived roles resolve from explicit SCIM memberships on each request.
Removing a member or deleting a group removes its derived access while preserving
independent user grants. ABAC `user_in_groups` and group-member counts use that
same authority. For SCIM-managed identifiers, this membership list overrides
SSO's group snapshot, including after removal or deletion. SSO-only groups remain
available. User deprovisioning clears SCIM memberships; reactivation requires the
IdP to re-add current groups.

Migration `astrolift_identity.0040_scim_groups` adds the Group and membership
tables, preserving the existing Member and RoleBinding schemas for the previous
app version. Apply migrations before starting the new app. Enable Group sync
after every app/worker has upgraded so every policy evaluation uses the new group
authority. No direct bindings are materialized or rewritten during migration.

The supported Group operations follow [RFC 7643 §4.2](https://www.rfc-editor.org/rfc/rfc7643.html#section-4.2)
and [RFC 7644 §3.5.2](https://www.rfc-editor.org/rfc/rfc7644.html#section-3.5.2).
