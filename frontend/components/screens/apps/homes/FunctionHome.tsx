"use client";

import { BoltIcon, GlobeIcon, ZapIcon } from "lucide-react";
import * as React from "react";

import {
  GoldenSignalsPanel,
  type GoldenSignalsPanelProps,
} from "@/components/observability/GoldenSignalsPanel";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

export interface FunctionHomeScreenProps {
  name: string;
  workload: Pick<AstroliftWorkload, "isPublic">;
  /** Public host when the function is HTTP-triggered; empty otherwise. */
  host: string | null;
  /** useGoldenSignals scoped to this function's workload. */
  goldenSignals: GoldenSignalsPanelProps;
  /** The app's tab bar. */
  tabs?: React.ReactNode;
}

/**
 * Primitive home for a **function** — serverless framing: how it's triggered
 * (HTTP endpoint or event) up top, then invocation history. Scale-to-zero, no
 * long-running deploy chrome.
 */
export function FunctionHomeScreen({
  name,
  workload,
  host,
  goldenSignals,
  tabs,
}: FunctionHomeScreenProps) {
  const url = workload.isPublic && host ? `https://${host}` : null;

  return (
    <PageShell
      title={
        <span className="flex items-center gap-3">
          <span className="bg-muted flex size-9 items-center justify-center rounded-md">
            <BoltIcon className="text-muted-foreground size-5" />
          </span>
          <span>{name}</span>
          <Badge variant="outline" className="gap-1.5">
            <BoltIcon className="size-3" />
            Function
          </Badge>
        </span>
      }
      description={
        <span className="text-muted-foreground text-xs">
          Serverless — runs on invoke, scales to zero when idle.
        </span>
      }
    >
      {tabs}
      <div className="space-y-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <ZapIcon className="size-4" />
              Trigger
            </CardTitle>
          </CardHeader>
          <CardContent>
            {url ? (
              <div className="space-y-1">
                <div className="text-muted-foreground text-xs tracking-wide uppercase">
                  HTTP endpoint
                </div>
                <a
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 font-mono text-sm text-[var(--brand-primary)] hover:underline"
                >
                  <GlobeIcon className="size-3.5" />
                  {host}
                </a>
              </div>
            ) : (
              <p className="text-muted-foreground text-sm">
                Event-triggered (no public HTTP endpoint). Invoked by the platform or an upstream
                producer.
              </p>
            )}
          </CardContent>
        </Card>

        {/* Invocations / latency / errors — the golden signals scoped to this
            function's workload (traffic == invocation rate). Live from
            Prometheus; each panel renders its own empty/NOT_CONFIGURED state. */}
        <GoldenSignalsPanel {...goldenSignals} />
      </div>
    </PageShell>
  );
}
