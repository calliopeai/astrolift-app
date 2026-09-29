"use client";

import { GlobeIcon, ZapIcon } from "lucide-react";

import {
  GoldenSignalsPanel,
  type GoldenSignalsPanelProps,
} from "@/components/observability/GoldenSignalsPanel";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

export interface FunctionHomeScreenProps {
  /** The app's name; the frame above shows it. */
  name: string;
  workload: Pick<AstroliftWorkload, "isPublic">;
  /** Public host when the function is HTTP-triggered; empty otherwise. */
  host: string | null;
  /** useGoldenSignals scoped to this function's workload. */
  goldenSignals: GoldenSignalsPanelProps;
}

/**
 * The Overview for a **function**: how it is triggered (an HTTP endpoint or
 * an event) first, then its invocations, latency and errors. Runs on invoke,
 * scales to zero; no long-running deploy chrome.
 */
export function FunctionHomeScreen({ workload, host, goldenSignals }: FunctionHomeScreenProps) {
  const url = workload.isPublic && host ? `https://${host}` : null;

  return (
    <PanelGrid>
      <Panel
        title="Trigger"
        icon={<ZapIcon className="size-4" />}
        description="Runs on invoke and scales to zero when idle."
      >
        {url ? (
          <div className="min-w-0 space-y-1">
            <div className="text-muted-foreground text-xs tracking-wide uppercase">
              HTTP endpoint
            </div>
            <a
              href={url}
              target="_blank"
              rel="noreferrer"
              className="text-primary inline-flex min-w-0 items-center gap-1.5 font-mono text-sm [overflow-wrap:anywhere] hover:underline"
            >
              <GlobeIcon className="size-3.5 shrink-0" />
              <span className="min-w-0">{host}</span>
            </a>
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">
            Event-triggered (no public HTTP endpoint). Invoked by the platform or an upstream
            producer.
          </p>
        )}
      </Panel>

      {/* Invocations, latency and errors: the golden signals scoped to this
          function's workload (traffic is the invocation rate). Live from
          Prometheus; each signal renders its own empty or not-configured state. */}
      <div className="col-span-12 min-w-0">
        <GoldenSignalsPanel {...goldenSignals} />
      </div>
    </PanelGrid>
  );
}
