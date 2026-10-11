# Operator SMTP alert diagnostic

Open **Settings → Notifications → Install alert email test**
(`/settings/notifications?section=install-email`) in the current organization. The **Test platform alert email** link on the
Domain email delivery page uses the same advisory settings permission and opens
this exact section.
The navigation uses the existing `org.update` advisory permission; support,
history and send independently require the server's current platform-operator,
organization, credential and event-preference admission. An ordinary organization
administrator cannot use the diagnostic merely because its navigation is visible.

Choose one of the eight existing email event kinds. Review the configured sender,
your current registered mailbox, configured TLS mode and check time, then explicitly
select **Send reviewed alert test**. Recipient, message and relay configuration
are not editable. Support describes configuration and current permission; it does
not establish a successful SMTP connection. The install email master flag and current per-event email
preference must allow the test. Unsupported install backends and app-owned SMTP,
SES and Azure ACS remain separate channels.

The page rechecks the reviewed opaque source fingerprint before dispatch and
stores only an actor/organization/event-bound request UUID and fingerprint in
session storage. It never stores relay credentials, a subject or a message body.
Do not interpret a refusal or lost reply as proof that no message was accepted.
Refresh current own-caller history using the original request UUID. An unresolved
request cannot be resent from this UI. A new intentional test is available only
after a correlated accepted or known-failed receipt; reserved, sent and unknown
records remain recovery-only. A history read failure never unlocks the intent.

SMTP acceptance is not recipient delivery or inbox placement. The backend does
not observe delivery for this diagnostic. Previous accepted history does not
establish current transport health. The panel does not configure notification
preferences or verify alert scheduling, app SMTP transports or Azure ACS.
