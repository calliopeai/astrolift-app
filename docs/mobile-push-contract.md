# Mobile push contract

Every push notification Astrolift sends carries a versioned `data` map (#2161).
A client routes by it: which install sent the notification, which organization
it belongs to, what it is about and which screen a tap opens. Details are read
after the client authenticates to that install; the payload carries no
credential and nothing a tap could act on.

The machine-readable fixture is
[`backend/contracts/push-notifications.json`](../backend/contracts/push-notifications.json).
It is generated from the real dispatcher and drivers (see the end of this page);
mobile parser and routing tests can consume it as it is.

## Data keys

Every value is a string. Nothing else travels in `data`.

| Key | Sent | Meaning |
| --- | --- | --- |
| `payload_version` | always | `"1"`, the major version of this contract |
| `install_id` | always | The install that sent the push: the value its `astroliftServerInfo.installId` reports |
| `organization_id` | events that belong to an organization | The organization's GUID, the value of the `X-Astrolift-Organization` header |
| `event_kind` | always | The event type, also the notification preference key (session preferences add the client kind: `auth.session.created.mobile`) |
| `target_kind` | always | What the notification is about (table below) |
| `target_id` | when the event names one | The target's GUID |
| `action_url` | always | The deep link a tap opens, relative to the install |

The notification's title and body are the platform alert (below), not `data`
keys. They are display text and can name resources: an app, a host, the name of
a revealed secret, the start of an agent's message.

## Event kinds

| `event_kind` | `target_kind` | `action_url` | Sent today by |
| --- | --- | --- | --- |
| `agent.message.sent` | `agent_message` | `astrolift://inbox/<thread id>` | agent messages (#2434) |
| `alert.fired`, `alert.resolved` | `alert_event` | `astrolift://alerts/<alert event id>` | the alert engine |
| `app.down`, `app.recovered` | `app` | `astrolift://apps/<app slug>` | the uptime probe |
| `app.expiring` | `app` | `astrolift://apps/<app slug>` | temporary-app expiry (#2433) |
| `app.deregister_pending` | `app` | `astrolift://apps/<app slug>` | nothing yet |
| `auth.session.created` | `session` | `astrolift://sessions/<session id>` | sign-in (#499) |
| `cluster.bootstrap_failed` | `cluster` | `astrolift://clusters/<cluster id>` | nothing yet |
| `deploy.approved`, `deploy.rejected`, `deploy.failed` | `deployment` | `astrolift://deployments/<deployment id>` | nothing yet |
| `domain.cert_expiring`, `domain.cert_renewal_failed` | `custom_domain` | `astrolift://apps/<app slug>/domains` | the certificate monitor |
| `secret.revealed` | `secret` | `astrolift://audit?filter=secret.revealed` | nothing yet |

Without an id a link names the collection (`astrolift://alerts`,
`astrolift://sessions`). "Nothing yet" kinds have a template and a preference but
no code emits the event, so no device receives them today.

App links use the slug because apps are read by slug (`astroliftApp(slug)`).
`target_id` is the app's GUID: a deleted app's slug can be taken by a new app,
so compare the GUID of the app you opened before showing it as the subject of
the notification.

## Install identity

`install_id` is `ASTROLIFT_INSTALL_ID` when the operator sets it. Otherwise
it is generated once (`install-` and 24 hex digits) and stored in the install's
database, so the web replicas, the worker that sends pushes, and every restart
report the same value.

Before #2161 the fallback id was derived from the secret key and the host name,
which differ per replica and per restarted container. An install without
`ASTROLIFT_INSTALL_ID` therefore reports a new id once after upgrading; clients
read it from the handshake.

A database copied to stand up a second install carries the stored id. Set
`ASTROLIFT_INSTALL_ID` on the copy so the two installs stay distinct.

## Where the title and body go

`category` is the event kind. Drivers send it as the APNs `aps.category` and as
the Android notification channel id.

| Driver | Android | iOS |
| --- | --- | --- |
| `gcp_fcm` | `message.notification`; data in `message.data`; channel in `message.android.notification.channel_id` | `message.notification` and `message.apns.payload.aps.alert`; data becomes APNs custom keys |
| `aws_sns` | `GCM` document: `notification` (channel `android_channel_id`) and `data`, which SNS maps to an FCM v1 message | `APNS` and `APNS_SANDBOX` documents: `aps.alert`, data keys beside `aps` |
| `azure_anh` | `fcmv1` format: the FCM v1 message under `message` | `apple` format: `aps.alert`, data keys beside `aps` |
| `otlp_webhook` | bridge envelope: `payload.title`, `payload.body`, `payload.data` | the same |

An Android app shows a notification whose channel it has not created in FCM's
fallback channel. These categories carry no notification actions, and a client
must not register actions for them that act on the install.

## Routing

A client applies these rules in order. The fixture's `routing` cases cover each
one, including two installs that both hold an app called `web`.

1. `payload_version` missing or of a major version the client does not
   support: show the notification, do not route (`unsupported_payload_version`).
   Servers before this contract send no `payload_version`.
2. `install_id` missing: show the notification, do not route
   (`missing_install_id`). Never fall back to the active install.
3. `install_id` matches no install on the device (it was removed, or the user
   never signed in to it there): show the notification, do not route
   (`unknown_install`).
4. Otherwise, after unlock and authentication, open `action_url` in that install
   (switching to it, with confirmation, when another install is active) and in
   the organization named by `organization_id`. If the target is gone or not
   visible, say so.

A client that does not know an `event_kind` or cannot parse `action_url` still
applies rules 1 to 3, then opens the install without navigating. A notification
never performs an operation: approving, revoking a session or acknowledging an
alert happens in the opened screen, authenticated and confirmed.

When a user removes an install from a device, the client revokes that device's
registration on the install (`revokeMobileDevice`). Until it does, the install
keeps sending, and rule 3 shows those notifications without routing. Clients
built before this contract look for `type` and `deep_link`, find no known type,
and show the notification without navigating.

A new optional key keeps `payload_version`. Renaming or removing a key, or
changing what one means, bumps it.

## Changes from the earlier payload

`event_type` is now `event_kind`. `resource_kind`, `resource_id`,
`request_id`, `trace_id` and the per-event keys (`deployment_guid`,
`app_slug`, `environment_name`, `rule_guid`, `severity`, `session_guid`,
`client_kind`, `cluster_guid`, `app_guid`, `secret_key`, `hostname`,
`days_until_expiry`, `target_url`, `status_code`, `message_id`, `thread_id`,
`kind`, `expires_at`) are no longer sent. The session link moved from
`astrolift://settings/devices?session=<id>` to `astrolift://sessions/<id>`,
alert links carry the alert event id instead of the rule id, and
`app.deregister_pending` links by slug instead of GUID.

## The fixture

`backend/astrolift_operations/tests/test_push_contract_2161.py` builds the file
on a real database:

- `events`: one notification per event kind, as the device receives it (title,
  body, category, `data`). Alerts, sessions, app uptime and expiry, certificate
  warnings and agent messages go through their production emitters; the kinds
  sent by nothing yet use the payload keys their templates read.
- `deliveries`: the alert notification as each driver sends it to Android and
  iOS.
- `routing`: the cases above, built from two installs' real payloads.

Ids are fixed for the run, and `install_id` comes from `ASTROLIFT_INSTALL_ID`.
The test fails when the committed file differs from what the code produces.
Regenerate it from `backend/` with:

```bash
ASTROLIFT_WRITE_PUSH_CONTRACT=1 pytest astrolift_operations/tests/test_push_contract_2161.py
```
