import { CheckCircle2Icon, ExternalLinkIcon, ShieldCheckIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Section } from "@/components/ui/section";

// ---------------------------------------------------------------------------
// Zentinelle integration gateway
//
// Zentinelle is Calliope's standalone AI Agent GRC platform — Governance,
// Risk, and Compliance for AI agents running on Astrolift.
//
// Integration shape: TBD. Auth handshake / tenant delegation between the two
// Django services is under design. This page is the placeholder that will
// become the connection UI once the shape is decided.
// ---------------------------------------------------------------------------

const CAPABILITIES = ["governance", "risk", "compliance", "observability"] as const;

/** The Zentinelle integration gateway. Static: no data, no props. */
export function ZentinelleScreen() {
  const t = useTranslations("zentinelleScreen");
  return (
    <PageShell title={t("title")} description={t("description")}>
      {/* Hero */}
      <Card>
        <CardContent className="flex flex-col gap-6">
          <div className="flex items-center gap-3">
            <div className="bg-primary/10 flex size-12 items-center justify-center rounded-full">
              <ShieldCheckIcon className="text-primary size-6" />
            </div>
            <div>
              <p className="text-lg font-semibold">Zentinelle</p>
              <p className="text-muted-foreground text-sm">{t("tagline")}</p>
            </div>
          </div>

          <p className="text-muted-foreground max-w-2xl text-sm leading-relaxed">
            {t.rich("intro", {
              strong: (chunks) => <strong className="text-foreground">{chunks}</strong>,
            })}
          </p>

          {/* Integration status chip */}
          <div className="bg-muted/30 flex items-start gap-3 rounded-md border border-dashed px-4 py-3">
            <div className="bg-warning/20 mt-1 flex size-4 shrink-0 items-center justify-center rounded-full">
              <div className="bg-warning size-1.5 rounded-full" />
            </div>
            <div>
              <p className="text-sm font-medium">{t("statusTitle")}</p>
              <p className="text-muted-foreground mt-0.5 text-xs">{t("statusBody")}</p>
            </div>
          </div>

          <div className="flex flex-wrap gap-3">
            <Button asChild>
              <a
                href="https://github.com/calliopeai/zentinelle"
                target="_blank"
                rel="noopener noreferrer"
              >
                <ExternalLinkIcon className="mr-2 size-4" />
                {t("github")}
              </a>
            </Button>
            <Button asChild variant="outline">
              <a href="https://docs.astrolift.app" target="_blank" rel="noopener noreferrer">
                {t("docs")}
                <ExternalLinkIcon className="ml-2 size-4" />
              </a>
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* What Zentinelle adds */}
      <Section title={t("addsTitle")}>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {CAPABILITIES.map((cap) => (
            <Card key={cap} size="sm">
              <CardContent className="flex gap-3">
                <CheckCircle2Icon className="text-primary mt-0.5 size-4 shrink-0" />
                <div>
                  <p className="text-sm font-semibold">{t(`capabilities.${cap}.title`)}</p>
                  <p className="text-muted-foreground mt-1 text-xs leading-relaxed">
                    {t(`capabilities.${cap}.description`)}
                  </p>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </Section>

      {/* Division of responsibility */}
      <div className="bg-muted/20 text-muted-foreground rounded-md border p-4 text-xs leading-relaxed">
        {t.rich("division", {
          label: (chunks) => <span className="text-foreground font-medium">{chunks}</span>,
          em: (chunks) => <em>{chunks}</em>,
          strong: (chunks) => <strong className="text-foreground">{chunks}</strong>,
          code: (chunks) => <code className="bg-muted text-2xs rounded px-1">{chunks}</code>,
        })}
      </div>
    </PageShell>
  );
}
