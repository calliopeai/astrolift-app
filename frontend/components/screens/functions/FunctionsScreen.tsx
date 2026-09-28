"use client";

import { AlertCircleIcon, BarChart3Icon, GaugeIcon, LayersIcon, ScrollIcon } from "lucide-react";
import type * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";

import { FUNCTION_TABS, type FunctionTab, type useFunctionsTab } from "./use-functions";

export type FunctionsScreenProps = ReturnType<typeof useFunctionsTab> & {
  /** The Fleet tab body (FunctionWorkloadsTable with its data); rendered only on that tab. */
  fleet: React.ReactNode;
};

const TAB_LABELS: Record<FunctionTab, string> = {
  fleet: "Fleet",
  throughput: "Throughput",
  errors: "Errors",
  latency: "Latency",
  logs: "Logs",
};

const TAB_ICONS: Record<FunctionTab, React.ReactNode> = {
  fleet: <LayersIcon className="size-4" />,
  throughput: <BarChart3Icon className="size-4" />,
  errors: <AlertCircleIcon className="size-4" />,
  latency: <GaugeIcon className="size-4" />,
  logs: <ScrollIcon className="size-4" />,
};

/** /functions: event-driven container invocations, with gateway signal tabs. */
export function FunctionsScreen({ tab, setTab, fleet }: FunctionsScreenProps) {
  return (
    <PageShell
      title="Functions"
      description="Event-driven container invocations — scale to zero, trigger on HTTP, queues, or webhooks. The runtime layer hasn't shipped yet; these signal surfaces activate once it does."
    >
      {/* Tab strip — URL-synced via ?tab= param. "fleet" is the default
          and omitted from the URL to keep the canonical /functions link clean. */}
      <div
        role="tablist"
        aria-label="Function signal tabs"
        className="bg-muted/40 inline-flex flex-wrap rounded-md border p-1"
      >
        {FUNCTION_TABS.map((tabKey) => {
          const active = tab === tabKey;
          return (
            <button
              key={tabKey}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setTab(tabKey)}
              className={
                "inline-flex items-center gap-1.5 rounded px-3 py-1 text-sm font-medium transition " +
                (active
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground")
              }
            >
              {TAB_ICONS[tabKey]}
              {TAB_LABELS[tabKey]}
            </button>
          );
        })}
      </div>

      {tab === "fleet" && fleet}
      {tab === "throughput" && (
        <EmptyState
          icon={<BarChart3Icon className="size-5" />}
          title="Function Throughput"
          description="Requests per second and invocation count across all function workloads — by trigger source and app."
        />
      )}
      {tab === "errors" && (
        <EmptyState
          icon={<AlertCircleIcon className="size-5" />}
          title="Function Errors"
          description="Failed invocations with HTTP status code, error type, and trigger context."
        />
      )}
      {tab === "latency" && (
        <EmptyState
          icon={<GaugeIcon className="size-5" />}
          title="Function Latency"
          description="Cold start frequency, warm invocation latency (p50/p95/p99), and end-to-end duration."
        />
      )}
      {tab === "logs" && (
        <EmptyState
          icon={<ScrollIcon className="size-5" />}
          title="Function Logs"
          description="Invocation logs from function containers including request context and response code."
        />
      )}
    </PageShell>
  );
}
