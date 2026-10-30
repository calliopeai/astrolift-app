# Caller notification inbox

The `notifications.structured_inbox` capability advertises operations notification
catch-up and streaming (#2457). These are actual recipient-addressed
`astrolift_operations.Notification` rows. They are independent of agent messages,
task events, the organization event feed, and the legacy `notificationReceived`
subscription over `core.Notification`. There is no invented agent sender or task
association. Reading this inbox never marks an agent thread read.

Preconditions: the authenticated person's own session or a person API token for
the selected organization; a token must permit `app.read` through the
`read:apps`, `write:apps` or `admin` scope (`backend/core/permissions.py`); the
query runs through `astro api graphql`, the subscription over the WebSocket
endpoint below, and the console shows the same rows at `/settings/notifications`;
the install must have `astrolift_operations` migration 0037 applied.
Placeholders: `<install>` is the control plane's hostname; `<person API token>`
is the caller's token; `<cursor>` is the `endCursor` of the last fully
processed page, or `null` to start from retained history.

```sh
astro api graphql -q 'query($after: String) {
  astroliftMyNotificationsPage(after: $after, limit: 100) {
    items { id kind title body link readAt createdAt expiryWarning { appId appSlug expiresAt warningWindow } }
    endCursor hasMore
  }
}' --var after=<cursor>
```

```graphql
query CatchUp($after: String) {
  astroliftMyNotificationsPage(after: $after, limit: 100) {
    items {
      id kind title body link readAt createdAt
      expiryWarning { appId appSlug expiresAt warningWindow }
    }
    endCursor
    hasMore
  }
}

subscription Follow($after: String) {
  astroliftMyNotificationStream(after: $after) {
    items {
      id kind title body link readAt createdAt
      expiryWarning { appId appSlug expiresAt warningWindow }
    }
    endCursor
    hasMore
  }
}
```

The query scans at most 100 retained rows in ascending inbox order (default 50).
`after: null` starts at the beginning of retained history. Persist `endCursor`
after processing the whole batch and continue while `hasMore` is true. The
subscription accepts the same cursor, drains catch-up in bounded batches, then
checks the database every two seconds. Its initial empty batch supplies a cursor
for an empty inbox. It works across API/Temporal processes and replicas without
relying on the process-local broker. Database and permission errors propagate;
the stream does not silently discard them.

The caller must be the authenticated current person in the selected live tenant.
Current organization membership and active account are rechecked on every query
and stream poll. API tokens must currently permit `app.read` (`read:apps`,
`write:apps`, or `admin` through the existing scope mapping); the token's
organization and team ceiling still apply. This adds no token scope. Team-bound
tokens receive only notifications whose structured provenance proves they cover
that app; unstructured generic rows cannot establish that and are omitted.

Expiry warnings additionally require current app-read visibility, current
creator/team-recipient eligibility, and no current explicit per-app
`app_expiring` mute. An app or its owner becoming unavailable also hides its
warnings. A recipient list is never exposed. The legacy bounded
`astroliftMyNotifications(unreadOnly, limit)` query and notification-read
mutations use these same current-viewer checks. Old `app_expiring` rows without
stored provenance are omitted: the server cannot reconstruct trusted app
identity or authorization from their human text or link.

An empty page can advance its cursor past rows hidden by current authorization.
Always retain that cursor and obey `hasMore`, even when `items` is empty. A cursor
is signed and bound to the install, tenant, person and credential ceiling. A
different identity, team, token or changed scope set must restart with `null`;
malformed cursors are rejected. Gaining access later does not rewind an existing
cursor: use `null` to rescan retained history and deduplicate by notification ID.

The database serializes notification inserts per tenant/person using an internal
counter ledger and admission trigger. This covers rolling old writers and bulk
inserts, avoids late-commit gaps, and keeps sequences from being reused after
notification deletion. Both user and organization are required for every row,
including system notifications. The ledger is internal protocol state, with no
business-object API or soft-delete lifecycle; it is retained until the owning
account or organization is deleted. Apply migration operations 0037 before
starting the new reader. Nullable provenance and a database default on the new
sequence field preserve old-writer compatibility. Reverse migration removes the
new protocol fields/ledger, so clients must discard its cursors.

Delivery is at least once while the actual notification row remains retained and
the caller remains authorized. After disconnect, reconnect with the last fully
processed cursor; repeated IDs must be ignored. There is no client acknowledgment,
exactly-once display guarantee, push-delivery receipt, or indefinite retention
promise. Notification read-state changes are not new stream events. Use
`markNotificationRead` or `markAllNotificationsRead` explicitly when appropriate.

`expiryWarning` snapshots the actual app GUID/slug, absolute expiry and emitted
window (`24h` or `1h`). Within one expiry, only the most specific due unsent window
emits; earlier missed windows are recorded as sent, with no invented notification
for them. Extending or resetting expiry does not rewrite old warning rows. A new
due window creates a distinct notification ID and new expiry snapshot. Display
the snapshot's timestamp as historical warning provenance; fetch current app state
before offering an expiry action. Quiet hours suppress personal email/push only
for `agent.message.sent`. App-expiry warnings (`app.expiring`) retain their
configured external fan-out. Quiet hours do not suppress this inbox or authorize
any external delivery.

Studio should present these items as platform notifications. CLI consumers can
execute the query above using their authenticated GraphQL transport; no new CLI
command or legacy subscription is claimed by this change. WebSocket transport is
`wss://<install>/app/gql/config/ws/`, subprotocol `graphql-transport-ws`, with
`connection_init` payload `{ "authorization": "Bearer <person API token>" }`,
or an existing authorized browser session. Wait for `connection_ack`, then
subscribe. Authorization failures terminate the operation; reconnecting does not
restore revoked access.

## Verify

```sh
astro api graphql -q 'query { astroliftMyNotificationsPage(after: null, limit: 100) { items { id kind createdAt } endCursor hasMore } }'
```

The page must return a non-null `endCursor` even when `items` is empty, and a
second call with `--var after=<cursor>` must return only rows after it; an
unauthorized token exits non-zero with the GraphQL error on stderr.
