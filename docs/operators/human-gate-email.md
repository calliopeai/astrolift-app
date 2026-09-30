# Human-gate email delivery

Gate creation remains available when a notification channel fails. The
`workflow.human_gate.notified` event records each requested channel's boolean
delivery result, the workflow-run GUID and stage name. A refused mail send,
a transport returning zero deliveries, or no resolved recipient records
`delivery.email: false`, `status: failed` and event severity `error`.
Successful sends record `status: notified`; a configuration requesting no
channels records `status: skipped`. Email success means the configured transport
accepted one message, not proof that a recipient received it.

The event uses the standard organization-confined append-only event writer and
is visible through authorized activity/event reads. No recipient address or
provider exception is included in its payload. Persistence relies on the event
store being available; the general event writer remains best-effort during a
database failure. The gate stays RUNNING and can be reviewed despite delivery
failure. Activity retries may notify again; this is not an exactly-once send.

## SES configuration checklist

1. Set `FROM_EMAIL` to an address covered by a verified SES identity in the
   configured `AWS_SES_REGION_NAME`. Configure the same value on web and worker.
2. Permit `ses:GetSendQuota` on `*`, and `ses:SendEmail`/`ses:SendRawEmail` only
   on the chosen identity ARN, for the actual task/workload role.
3. Check the identity's sending, verification and DKIM status using SES APIs.
   Records in a duplicate hosted zone do not verify an identity: match the zone's
   NS records to the public delegated nameservers before planning TXT/DKIM changes.
4. Apply reviewed DNS/IaC changes from the operator's shell. No Terraform apply
   runs from an agent session. Resolve failed verification before enabling the
   sender; consult [SES identity verification](https://docs.aws.amazon.com/ses/latest/dg/creating-identities.html)
   and [DKIM troubleshooting](https://docs.aws.amazon.com/ses/latest/dg/troubleshoot-dkim.html).
5. Inspect the persisted gate notification event after an explicitly authorized
   test with an agreed recipient. A false email result is a visible failure,
   never a claim of successful delivery. Local tests use controlled transports
   and do not send external email or Slack messages.

## Conflict inspection, 2026-09-30 UTC

Read-only inspection for #1823 finds that `conflict-astrolift-task` already has
quota permission and identity-scoped send permission for
`astrolift.us-west-2.conflict.softinfra.net`. The SES identity reports failed
verification/DKIM and sending disabled. The publicly delegated Route53 zone is
`Z00297308K0JGG1ZSOAA`; the other public zone with the same name is not delegated.
The identity's `_amazonses` TXT record is absent from the delegated zone.

Web definition `:47` and worker definition `:37` contain no explicit sender or
SES settings in environment or secret references, so the backend's default
`FROM_EMAIL` is not the configured SES identity. Live sender verification,
operator-applied DNS records, paired task sender configuration and an authorized
external-mail test remain required. #1823 remains open; these local delivery
tests do not establish a working production sender.
