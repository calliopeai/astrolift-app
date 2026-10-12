import Link from "next/link";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

import { envelopeExample, type EventSpec, wrappedExample } from "./webhook-events-data";

export interface WebhookEventsScreenProps {
  events: EventSpec[];
}

const code = (chunks: ReactNode) => <code>{chunks}</code>;
const monoCode = (chunks: ReactNode) => <code className="font-mono">{chunks}</code>;
function docLink(href: string) {
  return function DocLink(chunks: ReactNode) {
    return (
      <Link href={href} className="text-foreground underline-offset-2 hover:underline">
        {chunks}
      </Link>
    );
  };
}

export function WebhookEventsScreen({ events }: WebhookEventsScreenProps) {
  const t = useTranslations("docsWebhookEvents");
  const categories = Array.from(new Set(events.map((e) => e.category)));

  return (
    <article className="flex max-w-3xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("title")}</h1>
        <p className="text-muted-foreground mt-2 text-sm">
          {t.rich("intro", { link: docLink("/documentation/webhooks") })}
        </p>
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("envelopeTitle")}</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t.rich("envelopeBody", { code })}
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{envelopeExample}</code>
        </pre>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>{t.rich("eventIdNote", { code: monoCode })}</li>
          <li>{t.rich("deliveryIdNote", { code: monoCode, plain: code })}</li>
          <li>{t.rich("schemaVersionNote", { code: monoCode })}</li>
        </ul>
      </section>

      <Separator />

      <nav className="flex flex-col gap-2">
        <h2 className="text-muted-foreground text-xs font-semibold tracking-wider uppercase">
          {t("onThisPage")}
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
                      <h3 className="text-foreground mb-2 text-sm font-medium">
                        {t("payloadFields")}
                      </h3>
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
                      <h3 className="text-foreground mb-2 text-sm font-medium">{t("example")}</h3>
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
        <h2 className="text-lg font-medium">{t("signatureTitle")}</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t.rich("signatureBody", {
            code,
            signed: "<timestamp>.<raw_body>",
            link: docLink("/documentation/webhooks"),
            badge: (chunks) => (
              <Badge variant="outline" className="font-mono">
                {chunks}
              </Badge>
            ),
          })}
        </p>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("subscribingTitle")}</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t.rich("subscribingBody", { code, link: docLink("/documentation/webhooks") })}
        </p>
        <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>{t.rich("wildcardAll", { code: monoCode })}</li>
          <li>{t.rich("wildcardCategory", { code: monoCode })}</li>
          <li>{t.rich("wildcardList", { code: monoCode })}</li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("relatedTitle")}</h2>
        <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>{t.rich("relatedWebhooks", { link: docLink("/documentation/webhooks") })}</li>
          <li>{t.rich("relatedPolicies", { link: docLink("/documentation/policies") })}</li>
        </ul>
      </section>
    </article>
  );
}
