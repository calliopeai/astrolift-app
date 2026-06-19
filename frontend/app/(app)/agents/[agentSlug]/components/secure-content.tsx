"use client";

import { ExternalLinkIcon, ShieldCheckIcon } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

const CAPABILITIES = [
  "Policy enforcement — PII, toxicity, and jailbreak detection; model allowlists; token budgets.",
  "Risk register, anomaly detection, and alerts on agent behavior drift.",
  "SOC 2 / GDPR / HIPAA / EU AI Act control mappings with audit-ready evidence.",
  "Full interaction audit — prompts, responses, and tool calls.",
];

/**
 * Secure tab content for an agent (spec 33 PR-9).
 *
 * The Secure pillar is owned by Zentinelle — Calliope's AI Agent GRC product —
 * which is a deferred/enterprise integration (its auth handshake is still
 * under design; see `/secure/zentinelle`). This is the gate placeholder:
 * it frames what the pillar will provide and links to the full Zentinelle
 * page, reusing that page's gate framing rather than fabricating governance
 * data Astrolift doesn't own.
 */
export function SecureContent({ agentName }: { agentName: string }) {
  return (
    <Card>
      <CardContent className="flex flex-col gap-6 p-8">
        <div className="flex items-center gap-3">
          <div className="bg-primary/10 flex size-12 items-center justify-center rounded-full">
            <ShieldCheckIcon className="text-primary size-6" />
          </div>
          <div>
            <p className="text-lg font-semibold">Governed by Zentinelle</p>
            <p className="text-muted-foreground text-sm">
              AI Agent GRC — the Secure pillar of the BROCS stack
            </p>
          </div>
        </div>

        <p className="text-muted-foreground max-w-2xl text-sm leading-relaxed">
          Policy enforcement, content scanning, compliance mappings, and the full interaction
          audit trail for <span className="text-foreground font-medium">{agentName}</span> are
          provided by Zentinelle, which runs alongside Astrolift. Astrolift owns the runtime
          layer; Zentinelle owns the AI behavior layer.
        </p>

        {/* Deferred-integration gate. */}
        <div className="bg-muted/30 flex items-start gap-3 rounded-md border border-dashed px-4 py-3">
          <div className="bg-amber-500/20 mt-1 flex size-4 shrink-0 items-center justify-center rounded-full">
            <div className="bg-amber-500 size-1.5 rounded-full" />
          </div>
          <div>
            <p className="text-sm font-medium">Integration under design</p>
            <p className="text-muted-foreground mt-0.5 text-xs leading-relaxed">
              The auth handshake and tenant delegation between Astrolift and Zentinelle is being
              designed. Once it ships, per-agent governance, risk, and compliance surfaces appear
              here. Until then this pillar is a placeholder.
            </p>
          </div>
        </div>

        <ul className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {CAPABILITIES.map((cap) => (
            <li
              key={cap}
              className="text-muted-foreground flex gap-2 text-xs leading-relaxed"
            >
              <ShieldCheckIcon className="text-primary mt-0.5 size-3.5 shrink-0" />
              <span>{cap}</span>
            </li>
          ))}
        </ul>

        <div className="flex flex-wrap gap-3">
          <Button asChild variant="outline">
            <Link href="/secure/zentinelle">
              Learn about Zentinelle
              <ExternalLinkIcon className="ml-2 size-4" />
            </Link>
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
