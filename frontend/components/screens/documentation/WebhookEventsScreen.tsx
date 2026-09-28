import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

import { envelopeExample, type EventSpec, wrappedExample } from "./webhook-events-data";

export interface WebhookEventsScreenProps {
  events: EventSpec[];
}

export function WebhookEventsScreen({ events }: WebhookEventsScreenProps) {
  const categories = Array.from(new Set(events.map((e) => e.category)));

  return (
    <article className="flex max-w-3xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">Webhook events</h1>
        <p className="text-muted-foreground mt-2 text-sm">
          Every event type Astrolift emits via outbound webhooks, with the payload schema and a
          realistic JSON example for each. For setup and signature verification, see the{" "}
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
          Every delivery shares the same outer envelope. The event-specific fields live under{" "}
          <code>payload</code>. Optional envelope fields may be empty strings; client receivers
          should treat missing / empty as the absence of a value.
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{envelopeExample}</code>
        </pre>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>
            <code className="font-mono">event_id</code> is a ULID — globally unique, time-sortable.
            Use it for idempotency keys on your side.
          </li>
          <li>
            <code className="font-mono">delivery_id</code> is unique per attempt; retries reuse the
            same <code>event_id</code> with a new <code>delivery_id</code>.
          </li>
          <li>
            <code className="font-mono">schema_version</code> follows semver; payload-breaking
            changes bump the major.
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
                  <article key={event.name} id={anchor} className="flex flex-col gap-3">
                    <div className="flex items-center gap-2">
                      <Badge variant="outline" className="font-mono text-xs">
                        {event.name}
                      </Badge>
                    </div>
                    <p className="text-muted-foreground text-sm leading-relaxed">{event.when}</p>

                    <div>
                      <h3 className="text-foreground mb-2 text-sm font-medium">Payload fields</h3>
                      <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
                        {event.payloadFields.map((f) => (
                          <li key={f.name} className="leading-relaxed">
                            <code className="text-foreground">{f.name}</code>{" "}
                            <span className="text-muted-foreground text-xs">({f.type})</span> —{" "}
                            {f.description}
                          </li>
                        ))}
                      </ul>
                    </div>

                    <div>
                      <h3 className="text-foreground mb-2 text-sm font-medium">Example</h3>
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
          header (HMAC-SHA256 of <code>{`<timestamp>.<raw_body>`}</code>, hex-encoded, prefixed with{" "}
          <code>sha256=</code>). The{" "}
          <Link
            href="/documentation/webhooks"
            className="text-foreground underline-offset-2 hover:underline"
          >
            webhooks guide
          </Link>{" "}
          covers verification in Python, Node, and Go — read the raw body before any JSON parsing,
          and use a constant-time compare.
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
          steps. The event-type field accepts either the exact slug (e.g.{" "}
          <code>deployment.failed</code>) or a wildcard:
        </p>
        <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>
            <code className="font-mono">*</code> — every event.
          </li>
          <li>
            <code className="font-mono">deployment.*</code> — every event in the deployments
            category.
          </li>
          <li>
            <code className="font-mono">deployment.failed,alert.fired</code> — comma-separated list
            (also accepts whitespace).
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
            — create a subscription, verify signatures, troubleshoot deliveries.
          </li>
          <li>
            <Link
              href="/documentation/policies"
              className="text-foreground underline-offset-2 hover:underline"
            >
              ABAC policies
            </Link>{" "}
            — restrict who can create webhook subscriptions in your organization.
          </li>
        </ul>
      </section>
    </article>
  );
}
