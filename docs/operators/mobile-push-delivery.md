# Operator-hosted mobile push

An install can send push notifications to iPhone, iPad and Android itself,
with no SNS, Notification Hubs, Expo or other relay in between (#2256). The
`apns` driver talks to Apple's Push Notification service directly over
HTTP/2. The `gcp_fcm` driver talks to Firebase Cloud Messaging HTTP v1 with
access tokens minted from a service-account key, so it runs on any cloud or
none. Apple and Google still carry each notification to the device: hosting
the sender does not replace APNs or FCM.

Preconditions: `org.update` in the active organization for
`setNotificationProfile`; a kubeconfig that can edit the api and worker
Deployments in the install's namespace to mount the Secret; a shell in an api
or worker pod for `manage.py notification_driver_check`. Placeholders:
`<organization GUID>` is `id` from `astroliftOrganizations` (the health check
prints it too); `<organization-slug>` is its `slug`.

What a push carries is the versioned contract in
[mobile push contract](../mobile-push-contract.md): routing identity and a
link, never a credential or an action.

## Whose credentials

A push token belongs to the app that asked for it. On iOS and iPadOS that is
the app's bundle ID and the Apple Developer team that owns it; on Android, the
Firebase project in the app's `google-services.json`. Only an APNs key of that
team, and a service account allowed to send for that project, can deliver to
the token.

The distributed Astrolift Mobile build (bundle ID and package `ai.astrolift`)
is signed by Calliope Labs, so its tokens take Calliope Labs' credentials. An
install that sends with its own credentials reaches an **operator-owned
build**: astrolift-mobile built with `APP_BASE_BUNDLE_ID` and
`APP_BASE_PACKAGE` set to the operator's bundle ID and package, signed by the
operator's Apple team, with the operator's Firebase project configuration.
Reaching the distributed build from an operator install is an open decision
on #2256.

A profile names the one app it serves (`topic`, `project_id`). A token from
another app fails, and is never pruned: APNs answers
`DeviceTokenNotForTopic`, FCM `SENDER_ID_MISMATCH`.

## Credential files

The drivers read their keys from files, never from the profile:

- `ASTROLIFT_NOTIFICATION_SECRETS_DIR`, default
  `/var/run/secrets/astrolift/notifications`, on the web and worker processes.
- An organization's `auth_key_secret_ref` (APNs) and `credentials_secret_ref`
  (FCM) each name one file in `<that directory>/<organization GUID>/`. A name
  is letters, digits, `.`, `_` and `-`, does not start with `.`, and has no
  path, so a profile cannot read another organization's keys or any other
  file.
- The files are read again for every event that sends pushes. A rotated
  Secret applies to the next event, with no restart.

`python manage.py notification_driver_check --organization <slug>` prints the
directory (see [Health check](#health-check)). The GUID is also the
`organization_id` in every push.

```bash
python manage.py notification_driver_check --organization <organization-slug>
```

Mount a Kubernetes Secret per organization into the api (web) and worker
Deployments; both send pushes, for the events they emit:

```bash
kubectl -n astrolift create secret generic astrolift-push-acme \
  --from-file=apns-auth-key.p8=AuthKey_ABC123DEFG.p8 \
  --from-file=fcm-service-account.json=acme-push-sa.json
```

```yaml
# api and worker pod specs
volumes:
  - name: push-acme
    secret:
      secretName: astrolift-push-acme
containers:
  - name: api # or worker
    volumeMounts:
      - name: push-acme
        mountPath: /var/run/secrets/astrolift/notifications/<organization GUID>
        readOnly: true
```

Mount the whole Secret, not single keys with `subPath`: the kubelet updates a
mounted Secret's files in place, and a `subPath` mount never sees the update.
External Secrets Operator or the Secrets Store CSI driver can fill the same
directory from Vault or a cloud secret store.

Neither control-plane chart mounts extra volumes on the api and worker today
(`astrolift-opscode/helm/astrolift`, and the installer's
`terraform/k8s/astrolift-control-plane/chart`). Each needs a values hook for
these volumes before this works there; until then, patch the Deployments.

## Profiles

Set an organization's profile with `setNotificationProfile` (`org.update`).
The usual one serves both platforms: `apns` first, for iPhone and iPad, and
`gcp_fcm` for Android. APNs reports Android tokens as unsupported, so the
multiplexer hands them to FCM. Keep `apns` first: FCM would take the iOS
tokens, which astrolift-mobile registers as raw APNs tokens, and fail them.

```graphql
mutation SetPush($input: SetNotificationProfileInput!) {
  setNotificationProfile(input: $input) {
    ok
    errors { code field message }
    data { driver }
  }
}
```

```json
{
  "input": {
    "driver": "multiplexer",
    "config": {
      "primary": {
        "driver": "apns",
        "config": {
          "team_id": "DEF123GHIJ",
          "key_id": "ABC123DEFG",
          "topic": "com.example.astrolift",
          "environment": "production",
          "auth_key_secret_ref": "apns-auth-key.p8"
        }
      },
      "secondaries": [
        {
          "driver": "gcp_fcm",
          "config": {
            "project_id": "example-astrolift",
            "credentials_secret_ref": "fcm-service-account.json"
          }
        }
      ]
    }
  }
}
```

`apns` keys:

| Key | Value |
| --- | --- |
| `team_id` | The 10-character Team ID of the Apple Developer team that owns the app |
| `key_id` | The 10-character Key ID of the APNs auth key (`.p8`) |
| `topic` | The app's bundle ID |
| `environment` | `production` or `sandbox` (next section) |
| `auth_key_secret_ref` | The file holding the `.p8` key |
| `default_ttl_seconds` | Optional, default 86400: how long Apple keeps trying an offline device. `0` means one attempt, never stored |
| `timeout_seconds` | Optional, default 10, at most 60 |
| `max_attempts` | Optional, default 2, at most 3: sends of one notification, the first included |

Any other key is refused, a pasted `auth_key` included.

`gcp_fcm` keys: `project_id`, the Firebase project's id, and
`credentials_secret_ref`, a service-account JSON key whose account holds
**Firebase Cloud Messaging API Admin** (`roles/firebasecloudmessaging.admin`)
on that project. Tokens carry the `firebase.messaging` scope and are reused
until a few minutes before Google's one-hour expiry. Without
`credentials_secret_ref` the driver sends the profile's literal
`access_token`, which expires within the hour.

## APNs sandbox and production

App Store and TestFlight builds get production tokens; development builds
(Xcode, `expo run:ios`, development clients) get sandbox tokens. A profile
sends to one environment, and a token from the other answers
`BadDeviceToken`, recorded as a failed send and never pruned. Point
development builds at an install or organization whose profile says
`sandbox`.

Apple's team-scoped keys can be limited to one environment, and topic keys to
some bundle IDs. A key used against the other environment answers
`BadEnvironmentKeyIdInToken`.

## Who receives what

The app registers its token with the active organization each time it starts
(`registerMobileDevice`), and the row records that organization's driver. An
event goes to the recipient's devices registered in the event's organization,
through that organization's driver, and only to rows recorded with that
driver:

- A device registered in another organization is left to that organization:
  its pushes, its driver, its audit log. Sending it through this
  organization's credentials could hand its token to the wrong provider
  account, which then reported it invalid and pruned it.
- After a profile's driver changes (`gcp_fcm` to `multiplexer`, say), older
  rows are skipped as `driver_mismatch` until each app starts again and
  registers. Nothing is pruned meanwhile.
- A token is registered once per user, under the organization active when the
  app last registered. A user in two organizations with one phone receives
  the pushes of that organization only.

Notification preferences, quiet hours for agent messages and
`revokeMobileDevice` apply as for every driver.

## Outbound connectivity

The api and worker pods need TCP 443 to:

| Driver | Hosts |
| --- | --- |
| `apns` | `api.push.apple.com` (production), `api.sandbox.push.apple.com` (sandbox) |
| `gcp_fcm` | `fcm.googleapis.com`, and the key file's `token_uri` (`oauth2.googleapis.com`) |

APNs refuses HTTP/1.1. A `CONNECT` proxy (`HTTPS_PROXY`) works; a proxy that
terminates TLS must speak HTTP/2 to Apple. Each process keeps one HTTP/2
connection pool per APNs key, as Apple asks, and signs a new provider token
every 50 minutes.

## Health check

Run it in an api or worker pod, the processes that read the files:

```console
$ python manage.py notification_driver_check --organization acme
organization: acme (0190f0c2-...)
driver: multiplexer
secrets directory: /var/run/secrets/astrolift/notifications/0190f0c2-...
health: ok -- primary:apns=ok | gcp_fcm=ok
```

It builds the driver the organization's pushes would use and runs its health
check; it sends nothing to a device. `apns` proves the key file loads and
signs a provider token; Apple's verdict on the key shows on the first send.
`gcp_fcm` mints an access token from the service-account key, a real call to
Google. A missing profile, a missing or unreadable file, a malformed key or a
failed check exits non-zero with the reason.

## Delivery status

Every decision writes a `notification.push` row to the organization's audit
log: `status`, `detail`, `driver`, `recipient_user_id` and `token_last_4`,
never the token.

| `status` | Meaning |
| --- | --- |
| `delivered` | Apple or Google accepted it; `detail` is the message id |
| `invalid_token` | APNs `410 Unregistered` or `ExpiredToken`, FCM `UNREGISTERED`. The registration is pruned (`stale_at`); the app registers a new token |
| `failed` | `detail` says why: `APNs 400 BadDeviceToken` (malformed, or the other environment), `DeviceTokenNotForTopic` (another app), `403 InvalidProviderToken` (wrong `team_id` or `key_id`, or a revoked key), `ExpiredProviderToken` after a new token (the host clock), `APNs 5xx` or `APNs unreachable: <error>` (retriable), or FCM's HTTP error with its code, such as `(PERMISSION_DENIED SENDER_ID_MISMATCH)` |
| `rate_limited` | APNs `429 TooManyRequests` or FCM `RESOURCE_EXHAUSTED` |
| `unsupported` | No driver in the profile serves the platform: an Android device under an `apns`-only profile |
| `driver_mismatch` | The row was registered for another driver; see above |
| `no_driver` | No active profile, or its driver cannot be built. The web or worker log says why (`notification driver build failed`); so does the health check |
| `no_device` | The recipient has no live device registered in this organization |
| `opt_out`, `quiet_hours`, `excluded` | The recipient's preference, agent-message quiet hours, the device that just signed in |
| `error` | The driver raised; the log has the traceback |

Only `invalid_token` prunes, and a Notification Hubs (`azure_anh`) send never
returns it: a hub send targets a tag, so its 400, 403, 404, 410 and 413 are
`failed` with what to check (a 404 means no such hub: check `namespace` and
`hub_name`). A `failed` or `rate_limited` send marked
retriable is not sent again later: there is no delivery queue yet. Apple asks
providers to wait 15 minutes before repeating a `5xx`. Within one send, the
driver repeats only a request that got no answer, or one answered
`ExpiredProviderToken`, up to `max_attempts`, under the same `apns-id` and
collapse id, so a copy Apple accepted twice shows once.

## Rotation and revocation

- **APNs key.** Create the new key, add it to the Secret under a new file
  name, then point the profile at it (`key_id`, `auth_key_secret_ref`); the
  next event signs with it. Then revoke the old key in the developer account,
  and restart the api and worker pods: Apple asks providers to close the
  connections an old key opened.
- **FCM key.** Add a key to the service account, replace the file, then
  delete the old key in Google Cloud. The next event mints tokens from the new
  key.
- **Stopping a driver.** Remove the file: sends fail as `no_driver` and no
  registration is touched. A `multiplexer` profile is built whole, so a
  missing or unreadable file under either driver stops both platforms. To
  stop one platform, set the profile to the other driver alone; as after any
  driver change, each device then waits for its app to register again.
- **A device.** `revokeMobileDevice` (the app calls it when an install is
  removed from the device) soft-deletes the registration.

## Not here yet

- A delivery queue: pushes go out from the process that emits the event, and
  a retriable failure is recorded, not scheduled again.
- Per-organization registration of one token, for users in several
  organizations.
- UnifiedPush for Android devices without Google services.
- Acceptance on signed builds and physical devices, which is separate from
  these tests and from simulator payload injection.

## Verify

```bash
python manage.py notification_driver_check --organization <organization-slug>
astro api graphql -q 'query { astroliftAuditEventsPage(action: "notification.push", limit: 20) { items { occurredAt decision data } } }'
```

The health check prints `health: ok` for every driver in the profile and exits
zero; after an event that pushes, the audit rows carry `status: delivered` in
`data` for the recipient's devices, and any other `status` names the cause from
the table above.
