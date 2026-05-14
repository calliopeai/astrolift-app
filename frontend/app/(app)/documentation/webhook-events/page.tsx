import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

export const metadata = {
  title: "Webhook events · Documentation · Astrolift",
};

interface FieldSpec {
  name: string;
  type: string;
  description: string;
}

interface EventSpec {
  name: string;
  category: string;
  when: string;
  payloadFields: FieldSpec[];
  example: string;
}

const envelopeExample = `{
  "event_type": "deployment.started",
  "event_id": "evt_01J7K2A6P3RGBYZW8XV4HMQDFC",
  "delivery_id": "wd_01J7K2A6Q1F7AE6QHJVKZ6N2YS",
  "occurred_at": "2026-05-13T15:42:08.412Z",
  "schema_version": "1.0",
  "organization_id": "org_01J6PR7VZ3WGCYKQT8XJZ4FQVS",
  "app_slug": "checkout-api",
  "actor_user_id": "usr_01J6PR8C9MX2VEQAH5T7N3DKBZ",
  "payload": {
    "deployment_id": "dep_01J7K2A6P3RGBYZW8XV4HMQDFC",
    "...": "per-event payload below"
  }
}`;

const events: EventSpec[] = [
  // ===== deployments =====
  {
    name: "deployment.started",
    category: "Deployments",
    when: "Fires when a deployment Workflow transitions out of pending and begins applying.",
    payloadFields: [
      { name: "deployment_id", type: "string", description: "ULID of the Deployment row." },
      { name: "app_slug", type: "string", description: "Slug of the RegisteredApp." },
      { name: "environment", type: "string", description: "Environment name (prod, stg, preview, etc.)." },
      { name: "image_digest", type: "string", description: "Container image digest being applied." },
      { name: "trigger_kind", type: "string", description: "One of push, manual, scheduled, rollback." },
      { name: "git_sha", type: "string", description: "Source revision being deployed." },
    ],
    example: `{
  "deployment_id": "dep_01J7K2A6P3RGBYZW8XV4HMQDFC",
  "app_slug": "checkout-api",
  "environment": "prod",
  "image_digest": "sha256:9c2f...e1",
  "trigger_kind": "push",
  "git_sha": "8a3c92b"
}`,
  },
  {
    name: "deployment.succeeded",
    category: "Deployments",
    when: "Fires when post-deploy health checks pass and the Deployment row is marked complete.",
    payloadFields: [
      { name: "deployment_id", type: "string", description: "ULID of the Deployment row." },
      { name: "app_slug", type: "string", description: "Slug of the RegisteredApp." },
      { name: "environment", type: "string", description: "Environment name." },
      { name: "image_digest", type: "string", description: "Container image digest that was applied." },
      { name: "duration_ms", type: "number", description: "Wall-clock duration of the deploy in milliseconds." },
    ],
    example: `{
  "deployment_id": "dep_01J7K2A6P3RGBYZW8XV4HMQDFC",
  "app_slug": "checkout-api",
  "environment": "prod",
  "image_digest": "sha256:9c2f...e1",
  "duration_ms": 84321
}`,
  },
  {
    name: "deployment.failed",
    category: "Deployments",
    when: "Fires when the deployment Workflow exits in a failed terminal state.",
    payloadFields: [
      { name: "deployment_id", type: "string", description: "ULID of the Deployment row." },
      { name: "app_slug", type: "string", description: "Slug of the RegisteredApp." },
      { name: "environment", type: "string", description: "Environment name." },
      { name: "reason", type: "string", description: "Short machine-readable failure code (timeout, health_check, image_pull_backoff, …)." },
      { name: "error_message", type: "string", description: "Human-readable message; safe to surface in chat." },
      { name: "failed_phase", type: "string", description: "Which Workflow phase failed (prepare, apply, verify, …)." },
    ],
    example: `{
  "deployment_id": "dep_01J7K2A6P3RGBYZW8XV4HMQDFC",
  "app_slug": "checkout-api",
  "environment": "prod",
  "reason": "health_check",
  "error_message": "Readiness probe failed after 5 attempts",
  "failed_phase": "verify"
}`,
  },
  {
    name: "deployment.rolled_back",
    category: "Deployments",
    when: "Fires when a rollback Workflow finishes — either an automatic post-failure rollback or an operator-triggered one.",
    payloadFields: [
      { name: "deployment_id", type: "string", description: "ULID of the rollback Deployment row." },
      { name: "rolled_back_from", type: "string", description: "ULID of the Deployment being reverted away from." },
      { name: "rolled_back_to", type: "string", description: "ULID of the Deployment being restored." },
      { name: "app_slug", type: "string", description: "Slug of the RegisteredApp." },
      { name: "environment", type: "string", description: "Environment name." },
      { name: "automatic", type: "boolean", description: "true when triggered by a post-deploy failure hook." },
    ],
    example: `{
  "deployment_id": "dep_01J7K2BCA8WGB1XCC7RJZ4FN3F",
  "rolled_back_from": "dep_01J7K2A6P3RGBYZW8XV4HMQDFC",
  "rolled_back_to": "dep_01J7K28EQ8WGB1XCC7RJZ4FN3F",
  "app_slug": "checkout-api",
  "environment": "prod",
  "automatic": true
}`,
  },
  {
    name: "deployment.approval_required",
    category: "Deployments",
    when: "Fires when a deployment hits an approval gate and is paused waiting on an approver.",
    payloadFields: [
      { name: "deployment_id", type: "string", description: "ULID of the Deployment row." },
      { name: "app_slug", type: "string", description: "Slug of the RegisteredApp." },
      { name: "environment", type: "string", description: "Environment name." },
      { name: "approver_role", type: "string", description: "Role binding that satisfies the gate (e.g. Owner, Approver)." },
      { name: "approval_url", type: "string", description: "Direct link to the approval page in the UI." },
      { name: "expires_at", type: "string", description: "ISO 8601 timestamp; deploy auto-cancels if not approved by then." },
    ],
    example: `{
  "deployment_id": "dep_01J7K2A6P3RGBYZW8XV4HMQDFC",
  "app_slug": "checkout-api",
  "environment": "prod",
  "approver_role": "Approver",
  "approval_url": "https://astrolift.example.com/apps/checkout-api/deployments/dep_01J7K2A6P3RGBYZW8XV4HMQDFC/approve",
  "expires_at": "2026-05-13T18:42:08Z"
}`,
  },
  {
    name: "deployment.approved",
    category: "Deployments",
    when: "Fires when an authorized user (or service account) approves a paused deployment.",
    payloadFields: [
      { name: "deployment_id", type: "string", description: "ULID of the Deployment row." },
      { name: "approver_user_id", type: "string", description: "ULID of the user that approved." },
      { name: "approver_email", type: "string", description: "Email of the approving user (denormalized for chat readability)." },
      { name: "comment", type: "string", description: "Optional approval note." },
    ],
    example: `{
  "deployment_id": "dep_01J7K2A6P3RGBYZW8XV4HMQDFC",
  "approver_user_id": "usr_01J6PR8C9MX2VEQAH5T7N3DKBZ",
  "approver_email": "leo@example.com",
  "comment": "Verified change log; clearing for prod."
}`,
  },
  {
    name: "deployment.rejected",
    category: "Deployments",
    when: "Fires when an approver rejects a paused deployment. The deploy transitions to a terminal failed state.",
    payloadFields: [
      { name: "deployment_id", type: "string", description: "ULID of the Deployment row." },
      { name: "rejected_by_user_id", type: "string", description: "ULID of the rejecting user." },
      { name: "rejected_by_email", type: "string", description: "Email of the rejecting user." },
      { name: "reason", type: "string", description: "Free-form rejection reason." },
    ],
    example: `{
  "deployment_id": "dep_01J7K2A6P3RGBYZW8XV4HMQDFC",
  "rejected_by_user_id": "usr_01J6PR8C9MX2VEQAH5T7N3DKBZ",
  "rejected_by_email": "leo@example.com",
  "reason": "Schema migration not reviewed yet."
}`,
  },
  // ===== previews =====
  {
    name: "preview.created",
    category: "Previews",
    when: "Fires when a preview environment finishes building (PreviewBuildWorkflow.complete).",
    payloadFields: [
      { name: "preview_id", type: "string", description: "ULID of the preview row." },
      { name: "app_slug", type: "string", description: "Slug of the parent RegisteredApp." },
      { name: "pr_number", type: "number", description: "Source-provider PR / MR number." },
      { name: "branch", type: "string", description: "Source branch name." },
      { name: "preview_url", type: "string", description: "Public URL the preview is reachable at." },
      { name: "ttl_seconds", type: "number", description: "Seconds until the preview is auto-torn-down without activity." },
    ],
    example: `{
  "preview_id": "prv_01J7K2A6P3RGBYZW8XV4HMQDFC",
  "app_slug": "checkout-api",
  "pr_number": 481,
  "branch": "leo/fee-cap",
  "preview_url": "https://pr-481.checkout-api.astrolift.app",
  "ttl_seconds": 86400
}`,
  },
  {
    name: "preview.torn_down",
    category: "Previews",
    when: "Fires when PreviewTeardownWorkflow finishes deleting the preview's resources.",
    payloadFields: [
      { name: "preview_id", type: "string", description: "ULID of the preview row." },
      { name: "app_slug", type: "string", description: "Slug of the parent RegisteredApp." },
      { name: "pr_number", type: "number", description: "PR / MR number." },
      { name: "reason", type: "string", description: "One of pr_closed, ttl_expired, manual." },
    ],
    example: `{
  "preview_id": "prv_01J7K2A6P3RGBYZW8XV4HMQDFC",
  "app_slug": "checkout-api",
  "pr_number": 481,
  "reason": "pr_closed"
}`,
  },
  // ===== apps =====
  {
    name: "app.registered",
    category: "Apps",
    when: "Fires when a new RegisteredApp row is created.",
    payloadFields: [
      { name: "app_slug", type: "string", description: "Slug for the new app." },
      { name: "display_name", type: "string", description: "Human-readable name." },
      { name: "repo_url", type: "string", description: "Source repo URL (empty if not yet linked)." },
      { name: "default_branch", type: "string", description: "Repo's default branch." },
      { name: "owner_user_id", type: "string", description: "ULID of the user that registered the app." },
    ],
    example: `{
  "app_slug": "checkout-api",
  "display_name": "Checkout API",
  "repo_url": "https://github.com/acme/checkout-api",
  "default_branch": "main",
  "owner_user_id": "usr_01J6PR8C9MX2VEQAH5T7N3DKBZ"
}`,
  },
  {
    name: "app.updated",
    category: "Apps",
    when: "Fires when any RegisteredApp field changes (display name, subdomain, deploy_branch, manifest, etc.).",
    payloadFields: [
      { name: "app_slug", type: "string", description: "Slug of the app." },
      { name: "fields_changed", type: "string[]", description: "Names of the fields that changed in this update." },
      { name: "updated_by_user_id", type: "string", description: "ULID of the user that made the change." },
    ],
    example: `{
  "app_slug": "checkout-api",
  "fields_changed": ["deploy_branch", "subdomain"],
  "updated_by_user_id": "usr_01J6PR8C9MX2VEQAH5T7N3DKBZ"
}`,
  },
  {
    name: "app.deleted",
    category: "Apps",
    when: "Fires when an app is soft-deleted (deleted_at is set). The webhook envelope's app_slug is still populated.",
    payloadFields: [
      { name: "app_slug", type: "string", description: "Slug of the deleted app." },
      { name: "deleted_by_user_id", type: "string", description: "ULID of the deleter." },
    ],
    example: `{
  "app_slug": "checkout-api",
  "deleted_by_user_id": "usr_01J6PR8C9MX2VEQAH5T7N3DKBZ"
}`,
  },
  // ===== secrets =====
  {
    name: "secret.written",
    category: "Secrets",
    when: "Fires when a secret is created or rotated in the configured secrets backend.",
    payloadFields: [
      { name: "secret_path", type: "string", description: "Path of the secret (e.g. apps/checkout-api/STRIPE_KEY). The value is never included." },
      { name: "app_slug", type: "string", description: "Slug of the owning app (empty for org-level secrets)." },
      { name: "rotation_kind", type: "string", description: "One of create, update, rotate." },
      { name: "written_by_user_id", type: "string", description: "ULID of the user that wrote the secret." },
    ],
    example: `{
  "secret_path": "apps/checkout-api/STRIPE_SECRET_KEY",
  "app_slug": "checkout-api",
  "rotation_kind": "rotate",
  "written_by_user_id": "usr_01J6PR8C9MX2VEQAH5T7N3DKBZ"
}`,
  },
  {
    name: "secret.deleted",
    category: "Secrets",
    when: "Fires when a secret is removed from the secrets backend.",
    payloadFields: [
      { name: "secret_path", type: "string", description: "Path of the deleted secret." },
      { name: "app_slug", type: "string", description: "Slug of the owning app." },
      { name: "deleted_by_user_id", type: "string", description: "ULID of the deleter." },
    ],
    example: `{
  "secret_path": "apps/checkout-api/STRIPE_SECRET_KEY",
  "app_slug": "checkout-api",
  "deleted_by_user_id": "usr_01J6PR8C9MX2VEQAH5T7N3DKBZ"
}`,
  },
  // ===== managed services =====
  {
    name: "managed_service.provisioning",
    category: "Managed services",
    when: "Fires when the ProvisionManagedServiceWorkflow starts creating a service binding (RDS, Redis, OpenSearch, etc.).",
    payloadFields: [
      { name: "service_id", type: "string", description: "ULID of the ManagedServiceBinding row." },
      { name: "app_slug", type: "string", description: "Slug of the binding's app." },
      { name: "kind", type: "string", description: "Service kind (postgres, redis, opensearch, …)." },
      { name: "tier", type: "string", description: "Tier slug (dev-small, prod-standard, …)." },
      { name: "cloud", type: "string", description: "Target cloud (aws, gcp, azure)." },
    ],
    example: `{
  "service_id": "msv_01J7K2A6P3RGBYZW8XV4HMQDFC",
  "app_slug": "checkout-api",
  "kind": "postgres",
  "tier": "prod-standard",
  "cloud": "aws"
}`,
  },
  {
    name: "managed_service.ready",
    category: "Managed services",
    when: "Fires when the provisioning Workflow reports the service is healthy and its connection secret is available.",
    payloadFields: [
      { name: "service_id", type: "string", description: "ULID of the ManagedServiceBinding." },
      { name: "app_slug", type: "string", description: "Slug of the app." },
      { name: "kind", type: "string", description: "Service kind." },
      { name: "connection_secret_path", type: "string", description: "Secrets-backend path the connection info was written to." },
      { name: "duration_ms", type: "number", description: "Provisioning duration in ms." },
    ],
    example: `{
  "service_id": "msv_01J7K2A6P3RGBYZW8XV4HMQDFC",
  "app_slug": "checkout-api",
  "kind": "postgres",
  "connection_secret_path": "apps/checkout-api/managed/postgres",
  "duration_ms": 432109
}`,
  },
  {
    name: "managed_service.failed",
    category: "Managed services",
    when: "Fires when the provisioning Workflow exits in a failed terminal state.",
    payloadFields: [
      { name: "service_id", type: "string", description: "ULID of the ManagedServiceBinding." },
      { name: "app_slug", type: "string", description: "Slug of the app." },
      { name: "kind", type: "string", description: "Service kind." },
      { name: "reason", type: "string", description: "Machine-readable failure code." },
      { name: "error_message", type: "string", description: "Human-readable error message." },
    ],
    example: `{
  "service_id": "msv_01J7K2A6P3RGBYZW8XV4HMQDFC",
  "app_slug": "checkout-api",
  "kind": "postgres",
  "reason": "cloud_api_error",
  "error_message": "RDS rejected DBInstanceClass: db.t4g.micro not available in us-west-2a"
}`,
  },
  // ===== alerts =====
  {
    name: "alert.fired",
    category: "Alerts",
    when: "Fires when an AlertRule transitions from quiet to firing (a threshold is breached).",
    payloadFields: [
      { name: "alert_id", type: "string", description: "ULID of the Alert instance." },
      { name: "rule_id", type: "string", description: "ULID of the AlertRule that fired." },
      { name: "rule_name", type: "string", description: "Display name of the rule." },
      { name: "severity", type: "string", description: "One of info, warning, critical." },
      { name: "app_slug", type: "string", description: "Slug of the app the rule scopes to (empty for org-wide)." },
      { name: "summary", type: "string", description: "One-line description of what's wrong." },
      { name: "value", type: "number", description: "Metric value that crossed the threshold." },
      { name: "threshold", type: "number", description: "The configured threshold." },
    ],
    example: `{
  "alert_id": "alt_01J7K2A6P3RGBYZW8XV4HMQDFC",
  "rule_id": "ar_01J6PRZ8VVWGCYKQT8XJZ4FQVS",
  "rule_name": "checkout-api 5xx rate",
  "severity": "critical",
  "app_slug": "checkout-api",
  "summary": "5xx rate 0.083 exceeded threshold 0.020 over 5m",
  "value": 0.083,
  "threshold": 0.02
}`,
  },
  {
    name: "alert.acknowledged",
    category: "Alerts",
    when: "Fires when an operator acknowledges a firing alert, silencing further notifications until it resolves.",
    payloadFields: [
      { name: "alert_id", type: "string", description: "ULID of the Alert instance." },
      { name: "acknowledged_by_user_id", type: "string", description: "ULID of the acknowledger." },
      { name: "note", type: "string", description: "Free-form acknowledgement note." },
    ],
    example: `{
  "alert_id": "alt_01J7K2A6P3RGBYZW8XV4HMQDFC",
  "acknowledged_by_user_id": "usr_01J6PR8C9MX2VEQAH5T7N3DKBZ",
  "note": "Investigating; rolled back to dep_01J7K28EQ8WGB1XCC7RJZ4FN3F"
}`,
  },
  {
    name: "alert.resolved",
    category: "Alerts",
    when: "Fires when the underlying signal returns to a healthy state for the configured cool-down window.",
    payloadFields: [
      { name: "alert_id", type: "string", description: "ULID of the Alert instance." },
      { name: "duration_ms", type: "number", description: "How long the alert was firing." },
      { name: "auto_resolved", type: "boolean", description: "true when resolved by the metric returning to normal; false when an operator manually resolved it." },
    ],
    example: `{
  "alert_id": "alt_01J7K2A6P3RGBYZW8XV4HMQDFC",
  "duration_ms": 612000,
  "auto_resolved": true
}`,
  },
  // ===== domains =====
  {
    name: "domain.validation_pending",
    category: "Domains",
    when: "Fires when a custom domain is added and Astrolift is waiting on DNS validation.",
    payloadFields: [
      { name: "domain", type: "string", description: "FQDN being validated." },
      { name: "app_slug", type: "string", description: "Slug of the app the domain is bound to." },
      { name: "validation_method", type: "string", description: "One of dns-01, http-01." },
      { name: "record_name", type: "string", description: "DNS record name the operator must publish." },
      { name: "record_value", type: "string", description: "DNS record value." },
    ],
    example: `{
  "domain": "app.acme.com",
  "app_slug": "checkout-api",
  "validation_method": "dns-01",
  "record_name": "_acme-challenge.app.acme.com",
  "record_value": "xV3p9wB-...-K2"
}`,
  },
  {
    name: "domain.validated",
    category: "Domains",
    when: "Fires once the validation record is visible from the cert-manager resolvers.",
    payloadFields: [
      { name: "domain", type: "string", description: "FQDN that validated." },
      { name: "app_slug", type: "string", description: "Slug of the bound app." },
      { name: "validation_method", type: "string", description: "Method that succeeded." },
    ],
    example: `{
  "domain": "app.acme.com",
  "app_slug": "checkout-api",
  "validation_method": "dns-01"
}`,
  },
  {
    name: "domain.cert_issued",
    category: "Domains",
    when: "Fires when cert-manager issues (or renews) the certificate for the domain.",
    payloadFields: [
      { name: "domain", type: "string", description: "FQDN the certificate covers." },
      { name: "app_slug", type: "string", description: "Slug of the bound app." },
      { name: "issuer", type: "string", description: "cert-manager ClusterIssuer name (e.g. letsencrypt-prod)." },
      { name: "expires_at", type: "string", description: "ISO 8601 expiry timestamp of the new certificate." },
      { name: "renewal", type: "boolean", description: "true when this is a renewal of an existing cert." },
    ],
    example: `{
  "domain": "app.acme.com",
  "app_slug": "checkout-api",
  "issuer": "letsencrypt-prod",
  "expires_at": "2026-08-11T15:42:08Z",
  "renewal": false
}`,
  },
  // ===== synthetic =====
  {
    name: "webhook.test",
    category: "Synthetic",
    when: "Synthetic event emitted by the Test webhook button on a subscription. Use it to verify your receiver before relying on real events.",
    payloadFields: [
      { name: "kind", type: "string", description: 'Always "webhook.test".' },
      { name: "subscription_id", type: "string", description: "ULID of the subscription being tested." },
      { name: "organization_id", type: "string", description: "ULID of the organization (also present in the envelope)." },
      { name: "delivered_at", type: "string", description: "ISO 8601 timestamp the test fired at." },
      { name: "message", type: "string", description: "Static human-readable note." },
    ],
    example: `{
  "kind": "webhook.test",
  "subscription_id": "whs_01J7K2A6P3RGBYZW8XV4HMQDFC",
  "organization_id": "org_01J6PR7VZ3WGCYKQT8XJZ4FQVS",
  "delivered_at": "2026-05-13T15:42:08.412Z",
  "message": "test delivery from the Astrolift control plane"
}`,
  },
];

const wrappedExample = (event: EventSpec) =>
  `{
  "event_type": "${event.name}",
  "event_id": "evt_01J7K2A6P3RGBYZW8XV4HMQDFC",
  "delivery_id": "wd_01J7K2A6Q1F7AE6QHJVKZ6N2YS",
  "occurred_at": "2026-05-13T15:42:08.412Z",
  "schema_version": "1.0",
  "organization_id": "org_01J6PR7VZ3WGCYKQT8XJZ4FQVS",
  "payload": ${event.example
    .split("\n")
    .map((line, i) => (i === 0 ? line : `  ${line}`))
    .join("\n")}
}`;

const categories = Array.from(new Set(events.map((e) => e.category)));

export default function WebhookEventsDocPage() {
  return (
    <article className="flex max-w-3xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">Webhook events</h1>
        <p className="text-muted-foreground mt-2 text-sm">
          Every event type Astrolift emits via outbound webhooks, with the
          payload schema and a realistic JSON example for each. For setup
          and signature verification, see the{" "}
          <Link
            href="/documentation/webhooks"
            className="text-foreground underline-offset-2 hover:underline"
          >
            webhooks guide
          </Link>
          .
        </p>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Envelope shape</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Every delivery shares the same outer envelope. The event-specific
          fields live under <code>payload</code>. Optional envelope fields
          may be empty strings; client receivers should treat missing /
          empty as the absence of a value.
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{envelopeExample}</code>
        </pre>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>
            <code className="font-mono">event_id</code> is a ULID — globally
            unique, time-sortable. Use it for idempotency keys on your
            side.
          </li>
          <li>
            <code className="font-mono">delivery_id</code> is unique per
            attempt; retries reuse the same <code>event_id</code> with a
            new <code>delivery_id</code>.
          </li>
          <li>
            <code className="font-mono">schema_version</code> follows
            semver; payload-breaking changes bump the major.
          </li>
        </ul>
      </section>

      <Separator />

      <nav className="flex flex-col gap-2">
        <h2 className="text-muted-foreground text-xs font-semibold tracking-wider uppercase">
          On this page
        </h2>
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
          {categories.map((c) => (
            <li key={c}>
              <a
                href={`#cat-${c.toLowerCase().replace(/\s+/g, "-")}`}
                className="text-foreground underline-offset-2 hover:underline"
              >
                {c}
              </a>
            </li>
          ))}
        </ul>
      </nav>

      {categories.map((cat) => {
        const groupId = `cat-${cat.toLowerCase().replace(/\s+/g, "-")}`;
        return (
          <section key={cat} id={groupId} className="flex flex-col gap-6">
            <h2 className="text-lg font-medium">{cat}</h2>
            {events
              .filter((e) => e.category === cat)
              .map((event) => {
                const anchor = event.name.replace(/[.]/g, "-");
                return (
                  <article
                    key={event.name}
                    id={anchor}
                    className="flex flex-col gap-3"
                  >
                    <div className="flex items-center gap-2">
                      <Badge
                        variant="outline"
                        className="font-mono text-xs"
                      >
                        {event.name}
                      </Badge>
                    </div>
                    <p className="text-muted-foreground text-sm leading-relaxed">
                      {event.when}
                    </p>

                    <div>
                      <h3 className="text-foreground mb-2 text-sm font-medium">
                        Payload fields
                      </h3>
                      <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
                        {event.payloadFields.map((f) => (
                          <li key={f.name} className="leading-relaxed">
                            <code className="text-foreground">{f.name}</code>{" "}
                            <span className="text-muted-foreground text-xs">
                              ({f.type})
                            </span>{" "}
                            — {f.description}
                          </li>
                        ))}
                      </ul>
                    </div>

                    <div>
                      <h3 className="text-foreground mb-2 text-sm font-medium">
                        Example
                      </h3>
                      <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
                        <code>{wrappedExample(event)}</code>
                      </pre>
                    </div>
                  </article>
                );
              })}
          </section>
        );
      })}

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Signature verification</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Every delivery is signed with an{" "}
          <Badge variant="outline" className="font-mono">
            X-Astrolift-Signature
          </Badge>{" "}
          header (HMAC-SHA256 of{" "}
          <code>{`<timestamp>.<raw_body>`}</code>, hex-encoded, prefixed with{" "}
          <code>sha256=</code>). The{" "}
          <Link
            href="/documentation/webhooks"
            className="text-foreground underline-offset-2 hover:underline"
          >
            webhooks guide
          </Link>{" "}
          covers verification in Python, Node, and Go — read the raw body
          before any JSON parsing, and use a constant-time compare.
        </p>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Subscribing</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          To subscribe a receiver to one or more of these events, see the{" "}
          <Link
            href="/documentation/webhooks"
            className="text-foreground underline-offset-2 hover:underline"
          >
            Create a subscription
          </Link>{" "}
          steps. The event-type field accepts either the exact slug
          (e.g. <code>deployment.failed</code>) or a wildcard:
        </p>
        <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>
            <code className="font-mono">*</code> — every event.
          </li>
          <li>
            <code className="font-mono">deployment.*</code> — every event
            in the deployments category.
          </li>
          <li>
            <code className="font-mono">deployment.failed,alert.fired</code>{" "}
            — comma-separated list (also accepts whitespace).
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">Related</h2>
        <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>
            <Link
              href="/documentation/webhooks"
              className="text-foreground underline-offset-2 hover:underline"
            >
              Webhooks
            </Link>{" "}
            — create a subscription, verify signatures, troubleshoot
            deliveries.
          </li>
          <li>
            <Link
              href="/documentation/policies"
              className="text-foreground underline-offset-2 hover:underline"
            >
              ABAC policies
            </Link>{" "}
            — restrict who can create webhook subscriptions in your
            organization.
          </li>
        </ul>
      </section>
    </article>
  );
}
