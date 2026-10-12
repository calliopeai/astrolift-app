import Link from "next/link";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

const code = (chunks: ReactNode) => <code>{chunks}</code>;
const strong = (chunks: ReactNode) => <strong>{chunks}</strong>;
const strongTerm = (chunks: ReactNode) => <strong className="text-foreground">{chunks}</strong>;
const statusBadge = (chunks: ReactNode) => <Badge variant="secondary">{chunks}</Badge>;
function docLink(href: string) {
  return function DocLink(chunks: ReactNode) {
    return (
      <Link href={href} className="text-foreground underline-offset-2 hover:underline">
        {chunks}
      </Link>
    );
  };
}

/** Static "Custom domains" documentation page. */
export function CustomDomainsDoc() {
  const t = useTranslations("docsCustomDomains");
  const cert = useTranslations("apps.domains.cert");
  const step = (n: number, title: string) => t("stepHeading", { n, title });
  return (
    <article className="flex max-w-2xl flex-1 flex-col gap-6 p-6">
      <div>
        <h1 className="text-2xl font-semibold">{t("title")}</h1>
        <p className="text-muted-foreground mt-2 text-sm">{t.rich("intro", { code })}</p>
      </div>

      <div className="bg-muted/40 text-muted-foreground rounded-md border p-4 text-sm leading-relaxed">
        {t.rich("prereqNote", {
          strong: strongTerm,
          link: docLink("/documentation/cluster-prerequisites"),
        })}
      </div>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("whenTitle")}</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t.rich("whenBody", { code })}
        </p>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("prerequisitesTitle")}</h2>
        <ul className="text-muted-foreground flex flex-col gap-2 text-sm">
          <li>{t.rich("prereqCluster", { strong: strongTerm })}</li>
          <li>{t.rich("prereqDns", { strong: strongTerm })}</li>
          <li>{t.rich("prereqPermission", { code, strong: strongTerm })}</li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{step(1, t("step1Title"))}</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t.rich("step1Body", { strong })}
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{`/apps/<your-app>/domains  →  Add domain`}</code>
        </pre>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{step(2, t("step2Title"))}</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t.rich("step2Body", {
            badge: (chunks) => (
              <Badge variant="outline" className="mx-0.5 font-mono">
                {chunks}
              </Badge>
            ),
          })}
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{`Hostname:           app.acme.com
Validation method:  dns-01   (default)`}</code>
        </pre>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{step(3, t("step3Title"))}</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t.rich("step3Body", { code, badge: statusBadge })}
        </p>
        <pre className="bg-muted overflow-x-auto rounded-md p-4 text-xs leading-relaxed">
          <code>{`# dns-01
Type:   TXT
Name:   _acme-challenge.app.acme.com
Value:  <token from the Domains tab>
TTL:    300

# http-01
Type:   CNAME
Name:   app.acme.com
Value:  ingress.<cluster>.astrolift.app`}</code>
        </pre>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{step(4, t("step4Title"))}</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">{t("step4Body")}</p>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{step(5, t("step5Title"))}</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t.rich("step5Body", {
            strong,
            pending: statusBadge,
            validated: statusBadge,
            active: (chunks) => (
              <Badge className="bg-success/15 text-success-fg" variant="secondary">
                {chunks}
              </Badge>
            ),
          })}
        </p>
      </section>

      <Separator />

      {/* #1621 — the documented answer to "is my custom domain behind the
          login gate". It is not, it cannot be made to be by configuration,
          and an operator should read that here rather than discover it by
          opening the URL. */}
      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("authTitle")}</h2>
        <p className="text-muted-foreground text-sm leading-relaxed">{t("authBody")}</p>
        <p className="text-muted-foreground text-sm leading-relaxed">
          {t.rich("authMarker", {
            code,
            label: cert("edgeAuthUngated"),
            badge: (chunks) => (
              <Badge
                className="border-warning-border bg-warning/10 text-warning-fg"
                variant="outline"
              >
                {chunks}
              </Badge>
            ),
          })}
        </p>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("troubleshootingTitle")}</h2>
        <ul className="text-muted-foreground flex flex-col gap-3 text-sm">
          <li>
            <strong className="text-foreground">{t("troublePendingTitle")}</strong>{" "}
            {t("troublePendingBody")}
            <pre className="bg-muted mt-2 overflow-x-auto rounded-md p-3 text-xs leading-relaxed">
              <code>{`dig +short TXT _acme-challenge.app.acme.com
# expected: the validation token from the Domains tab`}</code>
            </pre>
          </li>
          <li>
            <strong className="text-foreground">{t("troubleValidationTitle")}</strong>{" "}
            {t("troubleValidationBody")}
          </li>
          <li>
            <strong className="text-foreground">{t("troubleCaaTitle")}</strong>{" "}
            {t.rich("troubleCaaBody", { code })}
          </li>
          <li>
            <strong className="text-foreground">{t("troubleExpiredTitle")}</strong>{" "}
            {t("troubleExpiredBody")}
          </li>
        </ul>
      </section>

      <Separator />

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-medium">{t("relatedTitle")}</h2>
        <ul className="text-muted-foreground flex flex-col gap-1.5 text-sm">
          <li>
            {t.rich("relatedSourceProviders", { link: docLink("/documentation/source-providers") })}
          </li>
          <li>
            {t.rich("relatedIdentityProviders", {
              link: docLink("/documentation/identity-providers"),
            })}
          </li>
        </ul>
      </section>
    </article>
  );
}
