# Agent environment spec home

Agents → Environment specs lists reusable container recipes across the active
organization. Rows link by slug to `/agents/environment-specs/<slug>` for their
runtime/image override, capability flags, owner kind, manifest source, and secret
reference bindings. This home is read-only; it does not resolve secret values or
launch a task.

`agentEnvironmentSpecsPage(orgId, search, filter, sort, page, pageSize)` applies
`agent_env_spec.read`, live-owner visibility, and bearer organization/team ceilings
before filtering, counting, and slicing. Organization-shared recipes remain visible
to local spec readers; owned recipes require their project or team. Foreign and
deleted ownership never falls through to a selected team. Counts cover the
authorized matching set, including recipes past the legacy list's 200-row cap.

Search matches name, slug, runtime, image override, and config repository. Filters
are `agentType`, `runtime`, and `createdBy` (user ids or `me`). Mine selects the
recorded creator, so older recipes with no creator remain in All. Sort keys are
`name`, `slug`, `runtime`, `created`, and `updated`, with shared numbered-page
validation and stable ties. The legacy `agentEnvironmentSpecs` list is unchanged.

The existing `agentEnvironmentSpec(slug)` detail read accepts an optional `orgId`
to validate the explicit organization. Existing clients may omit it. The home sends
both organization and slug, keeping identical slugs in separate client cache
entries. Missing or inaccessible recipes return null. Failed reads display an error
and Retry; a failed refresh can retain the last recipe in that same organization,
with the failure shown. Organization changes discard those displayed snapshots.

Verified with real PostgreSQL role/token/HTTP tests, a 205-recipe paging/search case,
revoked bindings and deleted owners, real Apollo refresh/organization-switch tests,
Storybook states and Chromium layout checks. This does not establish live-browser
authorization for a deployed install.

## Retired runtimes

Aider and OpenHands are retired because their integrations depend on LiteLLM.
They no longer appear in the selectable runtime catalog. New or updated recipes,
repository/import registrations, task preparation and box/job image resolution
refuse these runtime names and the known `astrolift-agent-aider` and
`astrolift-agent-openhands` image repositories, including mirrored, tagged,
digest-pinned and VNC variants. An explicit image does not bypass a retired
runtime name.

Existing recipes and task history remain readable, and existing tasks can still
be cancelled. Select an active runtime explicitly to reuse a retired recipe;
there is no automatic runtime or credential migration. Custom image contents
remain the image owner's responsibility: the control plane does not inspect
arbitrarily renamed images for embedded dependencies.
