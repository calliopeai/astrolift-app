import { CheckCircle2Icon, ExternalLinkIcon, ShieldCheckIcon } from "lucide-react";

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

const CAPABILITIES = [
  {
    title: "Governance",
    description:
      "Policy engine — content rules (PII, toxicity, jailbreak detection), model allowlists, token budget enforcement, and versioned system prompt library.",
  },
  {
    title: "Risk",
    description:
      "5×5 risk register, FMEA analysis, incident tracking, anomaly detection, and automated alerts on agent behavior drift.",
  },
  {
    title: "Compliance",
    description:
      "SOC 2, GDPR, HIPAA, EU AI Act, and NIST RMF control mappings. Evidence collection, gap analysis, and audit-ready reports.",
  },
  {
    title: "Observability",
    description:
      "Full interaction audit (prompts + responses + tool calls), content scan results, policy evaluation logs, and per-workload token usage metering.",
  },
];

/** The Zentinelle integration gateway. Static: no data, no props. */
export function ZentinelleScreen() {
  return (
    <PageShell
      title="Secure · Zentinelle"
      description="AI Agent GRC — Governance, Risk, and Compliance for every agent workload running on Astrolift."
    >
      {/* Hero */}
      <Card>
        <CardContent className="flex flex-col gap-6">
          <div className="flex items-center gap-3">
            <div className="bg-primary/10 flex size-12 items-center justify-center rounded-full">
              <ShieldCheckIcon className="text-primary size-6" />
            </div>
            <div>
              <p className="text-lg font-semibold">Zentinelle</p>
              <p className="text-muted-foreground text-sm">
                MIT-licensed · Self-hostable · Calliope companion product
              </p>
            </div>
          </div>

          <p className="text-muted-foreground max-w-2xl text-sm leading-relaxed">
            Zentinelle is the <strong className="text-foreground">Control + Secure</strong> pillar
            of the BROCS stack. It runs alongside Astrolift and provides the AI-specific layer that
            Astrolift deliberately does not own: policy enforcement, content scanning, compliance
            frameworks, and the full interaction audit trail that AI governance regulations require.
          </p>

          {/* Integration status chip */}
          <div className="bg-muted/30 flex items-start gap-3 rounded-md border border-dashed px-4 py-3">
            <div className="bg-warning/20 mt-1 flex size-4 shrink-0 items-center justify-center rounded-full">
              <div className="bg-warning size-1.5 rounded-full" />
            </div>
            <div>
              <p className="text-sm font-medium">Integration under design</p>
              <p className="text-muted-foreground mt-0.5 text-xs">
                The auth handshake and tenant delegation between Astrolift and Zentinelle is being
                designed — options include Zentinelle as an embedded Django app delegating auth to
                Astrolift, or a separate service with a token exchange. Once the shape is decided,
                this page becomes the connection management UI.
              </p>
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
                Zentinelle on GitHub
              </a>
            </Button>
            <Button asChild variant="outline">
              <a href="https://docs.astrolift.app" target="_blank" rel="noopener noreferrer">
                Integration docs
                <ExternalLinkIcon className="ml-2 size-4" />
              </a>
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* What Zentinelle adds */}
      <Section title="What Zentinelle adds to Astrolift">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {CAPABILITIES.map((cap) => (
            <Card key={cap.title} size="sm">
              <CardContent className="flex gap-3">
                <CheckCircle2Icon className="text-primary mt-0.5 size-4 shrink-0" />
                <div>
                  <p className="text-sm font-semibold">{cap.title}</p>
                  <p className="text-muted-foreground mt-1 text-xs leading-relaxed">
                    {cap.description}
                  </p>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </Section>

      {/* Division of responsibility */}
      <div className="bg-muted/20 text-muted-foreground rounded-md border p-4 text-xs leading-relaxed">
        <span className="text-foreground font-medium">Division of responsibility — </span>
        Astrolift owns the <em>runtime layer</em>: container orchestration, deployments, pod health,
        and infrastructure observability. Zentinelle owns the <em>AI behavior layer</em>: what the
        agent says and does, whether it complies with policy, and the audit trail regulators
        require. In <strong className="text-foreground">OBSERVE › Agents</strong>, tabs marked{" "}
        <code className="bg-muted text-2xs rounded px-1">Astrolift</code> are live today;{" "}
        <code className="bg-muted text-2xs rounded px-1">Zentinelle</code> tabs activate once the
        integration ships.
      </div>
    </PageShell>
  );
}
