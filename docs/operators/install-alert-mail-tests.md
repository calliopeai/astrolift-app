# Install alert-mail tests

This diagnostic tests the install's configured Django SMTP notification channel.
It is separate from app-owned SES delivery tests and `testNotificationChannel`,
which tests the inbox channel. The default `django_ses.SESBackend`, other backends,
and plaintext SMTP report unsupported. This API does not configure a relay or
send ordinary alerts on behalf of an application. Refs: #2289.

## Admission and source review

Select the current organization. Support, send, and history require its
`org.update` permission, an active platform operator and current organization
membership. Bearer credentials must also satisfy the existing admin scope and
organization ceiling. Browser session withdrawal and current credential state
are checked again during SMTP operations. Existing platform-operator policy
semantics remain unchanged.

`EMAIL_NOTIFICATIONS` must be enabled and the caller's `email` preference for
`eventKind` must allow the notification. Supported kinds are the existing
notification preference catalogue; the default is `deploy.failed`. The
recipient is the original caller's current registered mailbox. No recipient,
message body, subject, SMTP host, credentials, or authority override is accepted
in the mutation.

```graphql
query InstallAlertMailSupport {
  installAlertMailSupport(eventKind: "deploy.failed") {
    allowed reason transport sender recipient tlsMode sourceFingerprint checkedAt
  }
}
```

Support describes current install settings, not a successful connection or
recipient delivery. Review its sender, recipient, TLS mode and opaque source
fingerprint. The fingerprint uses the existing private signing key and a
versioned HMAC namespace. It binds the effective settings and event; it is not
an SMTP account or credential-incarnation proof. Client-certificate paths are
settings metadata, not a hash of the file contents. Source changes, preference
withdrawal, inactive membership or withdrawn credentials refuse new effects.

## Explicit send and recovery

Generate a request UUID once per intentionally reviewed test. Retain it through
response loss. Submit the exact reviewed fingerprint:

```graphql
mutation InstallAlertMailTest($input: SendInstallAlertMailTestInput!) {
  sendInstallAlertMailTest(input: $input) {
    ok errors { code message }
    data {
      id requestId version eventKind transport sender recipient status reasonCode
      createdAt acceptedAt deliveryObserved
    }
  }
}
```

The send service refuses an enclosing database transaction or disabled
autocommit with `ALERT_MAIL_ENCLOSING_TRANSACTION_UNSUPPORTED`, after current
permission admission and before reserving an intent or contacting SMTP. Its
intent and pre-DATA transitions commit independently before native effects;
callers must not wrap the service in a transaction that could discard nonce
history after sending.

The message uses the ordinary notice composer with fixed bounded text and its
test GUID as a correlation identifier. The receipt stores source/intent metadata,
addresses and state, with no subject, body, host, login password or native reply.
The org/request UUID uniqueness includes historical rows. A conflicting caller
or event cannot adopt that nonce. Replays under current original-source
admission return the same record and never reconnect or resend.

| Status | Meaning |
| --- | --- |
| `reserved` | Intent committed; the native attempt may still be in progress. |
| `sent` | Pre-DATA intent committed; SMTP acceptance is unconfirmed. A concurrent replay performs no send. |
| `accepted` | Original SMTP final DATA acknowledgement observed and privately recorded. |
| `failed` | Known refusal or failure; inspect the fixed reason. This request is never retried automatically. |
| `unknown` | Final acceptance was not confirmed after durable pre-DATA intent; never assume rejection or resend. |

Every record has `deliveryObserved=false`. SMTP acceptance transfers message
responsibility to the server; it does not demonstrate delivery to the recipient.
See [SMTP response semantics](https://www.rfc-editor.org/rfc/rfc5321.html#section-4.2.5).
A source/authority change after the original acceptance preserves that private
acknowledgement and refuses the public response. After a lost response, recover
through current own-caller history rather than creating a new test:

```graphql
query InstallAlertMailHistory($after: String, $limit: Int!) {
  installAlertMailTestsPage(eventKind: "deploy.failed", after: $after, limit: $limit) {
    items { id requestId version status reasonCode acceptedAt deliveryObserved }
    totalCount nextCursor
  }
}
```

The page is scoped to the original caller/current organization, capped at 50
rows, with a cursor bound to that caller, organization and event. Read-only
history remains available to an admitted operator after a source rotation;
replaying the send itself refuses the changed source. Do not infer current
transport health from an earlier accepted record.

## Install configuration and bounds

The existing `DJANGO_EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, and `FROM_EMAIL`
settings select the channel. Standard Django SMTP environment settings are
loaded explicitly: `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`,
`EMAIL_USE_SSL`, and optional `EMAIL_SSL_KEYFILE`/`EMAIL_SSL_CERTFILE`.
Exactly one of STARTTLS (`EMAIL_USE_TLS=true`) or implicit TLS
(`EMAIL_USE_SSL=true`) is required. TLS certificate and hostname verification
use Django's SSL context. Keep relay credentials in the install's existing
private configuration; they are never returned by this API.
See [Django SMTP configuration](https://docs.djangoproject.com/en/6.0/topics/email/).
Default backend/TLS/login values retain the existing SES configuration.

Each intentional new test is rate-limited to three per caller and ten per
organization per minute. There is one SMTP attempt and no automatic retry.
Socket operations have a maximum five-second timeout; the transport enforces a
15-second monotonic budget and shuts down its active socket on expiry. SMTP
multiline replies are limited to 64 lines and 32,768 total bytes. Client cleanup
closes sockets without issuing another SMTP command after acknowledgement or
withdrawal. System hostname resolution and PostgreSQL admission are external
synchronous services; the socket budget is not a hard end-to-end bound for a
stalled system resolver or database. Unknown transport outcomes remain honest.

Migration `0030_installalertmailtest` creates only the new receipt table and
constraints. Rollback refuses any retained receipt, including tombstones;
operators must keep that migration applied and use a forward fix. Do not delete
history to enable rollback. This leaf requires the normal source, migration and
security reviews before release, and does not complete SMTP app transports,
Azure ACS delivery, alert scheduling or inbox delivery verification.
