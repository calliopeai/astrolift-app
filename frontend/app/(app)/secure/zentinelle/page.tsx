import { ExternalLinkIcon, ShieldCheckIcon } from "lucide-react";

import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

export const metadata = { title: "Zentinelle · Astrolift" };

/**
 * Secure — Zentinelle integration gateway.
 *
 * Zentinelle is the GRC / security plane in the Calliope BROCS stack
 * (Build / Run / Observe / Control / Secure). It ingests Astrolift's
 * AuditEvent stream via webhook and provides compliance frameworks,
 * attestations, control testing, and agent-level security policies.
 *
 * This page is the handoff surface from Astrolift (Run) into Zentinelle.
 * When a Zentinelle deployment is connected, this page will surface the
 * live security posture for this install.
 */
export default function ZentinellePage() {
  return (
    <PageShell
      title="Zentinelle"
      description="Connect to a Zentinelle deployment to layer GRC, attestations, and security control testing onto this Astrolift install."
    >
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <ShieldCheckIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">Connect Zentinelle</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Point a Zentinelle deployment at this install to enable compliance
                frameworks, agent attestations, and control testing.
              </p>
            </div>
            <Button variant="outline" size="sm" className="w-fit gap-1.5" asChild>
              <a href="https://zentinelle.io" target="_blank" rel="noopener noreferrer">
                <ExternalLinkIcon className="size-3.5" />
                Learn more
              </a>
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="flex flex-col gap-4 p-6">
            <div className="bg-primary/10 text-primary w-fit rounded-md p-2.5">
              <ShieldCheckIcon className="size-5" />
            </div>
            <div>
              <p className="font-semibold">Audit event stream</p>
              <p className="text-muted-foreground mt-1 text-sm">
                Astrolift emits structured audit events on every state change.
                Zentinelle ingests these via webhook for continuous compliance
                monitoring.
              </p>
            </div>
            <Button variant="outline" size="sm" className="w-fit" asChild>
              <a href="/webhooks">Configure webhooks</a>
            </Button>
          </CardContent>
        </Card>
      </div>
    </PageShell>
  );
}
