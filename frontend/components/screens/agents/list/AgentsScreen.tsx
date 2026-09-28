"use client";

import {
  BarChart3Icon,
  BoxIcon,
  BrainIcon,
  ExternalLinkIcon,
  MonitorPlayIcon,
  ScrollIcon,
  ShieldCheckIcon,
  ZapIcon,
} from "lucide-react";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";

import { type AgentTab, TAB_LABELS, ZENTINELLE_TABS } from "./agents-list-tabs";
import type { useAgentsScreen } from "./use-agents-screen";

const TAB_ICONS: Partial<Record<AgentTab, React.ReactNode>> = {
  boxes: <BoxIcon className="size-4" />,
  theatre: <MonitorPlayIcon className="size-4" />,
  metrics: <BarChart3Icon className="size-4" />,
  logs: <ScrollIcon className="size-4" />,
  activity: <ShieldCheckIcon className="size-4" />,
  reasoning: <BrainIcon className="size-4" />,
  "token-usage": <ZapIcon className="size-4" />,
  compliance: <ShieldCheckIcon className="size-4" />,
};

/** The tabs whose content has data of its own, supplied by the route. */
export type AgentsScreenPanelTab =
  | "active"
  | "boxes"
  | "dispatch"
  | "history"
  | "registry"
  | "theatre";

export type AgentsScreenProps = ReturnType<typeof useAgentsScreen> & {
  /**
   * Content for the data-backed tabs. Only the selected tab's panel is
   * mounted, so each panel's queries run only while it is shown.
   */
  panels: Partial<Record<AgentsScreenPanelTab, React.ReactNode>>;
};

// ---------------------------------------------------------------------------
// Zentinelle-gated signal tabs
// ---------------------------------------------------------------------------

function ZentinelleGate({ tab }: { tab: AgentTab }) {
  const descriptions: Record<string, string> = {
    activity:
      "Policy evaluation results, content scans, blocked requests, and real-time agent behavior events — powered by Zentinelle's policy engine.",
    reasoning:
      "Full interaction audit: prompts, model responses, tool calls, chain-of-thought steps, and retry attempts — from Zentinelle's InteractionLog.",
    "token-usage":
      "Per-run and per-workload token consumption: input tokens, output tokens, cost attribution, and budget burn rate — from Zentinelle's cost meter.",
    compliance:
      "SOC2, GDPR, HIPAA, and EU AI Act controls mapped to this agent workload — from Zentinelle's compliance engine.",
  };
  return (
    <div className="flex flex-col items-center gap-4 rounded-lg border border-dashed p-8 text-center">
      <div className="bg-muted flex size-12 items-center justify-center rounded-full">
        {TAB_ICONS[tab]}
      </div>
      <div className="space-y-1">
        <p className="text-sm font-semibold">{TAB_LABELS[tab]} — powered by Zentinelle</p>
        <p className="text-muted-foreground max-w-md text-sm">{descriptions[tab as string]}</p>
      </div>
      <p className="text-muted-foreground bg-muted/40 max-w-sm rounded border px-3 py-2 text-xs">
        Connect Zentinelle to enable AI agent GRC observability. Integration under design.
      </p>
      <a
        href="https://github.com/calliopeai/zentinelle"
        target="_blank"
        rel="noopener noreferrer"
        className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1.5 text-xs"
      >
        Learn about Zentinelle <ExternalLinkIcon className="size-3" />
      </a>
    </div>
  );
}

/**
 * Agents — the fleet view: a URL-synced tab strip over Active runs, Boxes,
 * Dispatch, History, Registry, Theatre, the Metrics/Logs placeholders and the
 * Zentinelle-gated governance tabs.
 */
export function AgentsScreen({ visibleTabs, tab, onTabChange, panels }: AgentsScreenProps) {
  const panel = (panels as Partial<Record<AgentTab, React.ReactNode>>)[tab];
  return (
    <PageShell
      title="Agents"
      description="Dispatch, schedule, and monitor AI agent workloads across the fleet."
    >
      {/* Tab strip — URL-synced via ?tab= param. "active" is the default
          and omitted from the URL to keep the canonical /agents link clean. */}
      <div
        role="tablist"
        aria-label="Agent fleet tabs"
        className="bg-muted/40 inline-flex flex-wrap rounded-md border p-1"
      >
        {visibleTabs.map((tabKey) => {
          const active = tab === tabKey;
          return (
            <button
              key={tabKey}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => onTabChange(tabKey)}
              className={
                "inline-flex items-center gap-1.5 rounded px-3 py-1 text-sm font-medium transition " +
                (active
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground")
              }
            >
              {TAB_ICONS[tabKey]}
              {TAB_LABELS[tabKey]}
              {ZENTINELLE_TABS.has(tabKey) && (
                <Badge variant="secondary" className="text-2xs ml-0.5 h-4 px-1.5 py-0 font-normal">
                  Zentinelle
                </Badge>
              )}
            </button>
          );
        })}
      </div>

      {panel}
      {tab === "metrics" && (
        <EmptyState
          icon={<BarChart3Icon className="size-5" />}
          title="Agent Metrics"
          description="Dispatch rate, run duration (p50/p95), retry rate, and success counts — aggregated across all agent workloads."
        />
      )}
      {tab === "logs" && (
        <EmptyState
          icon={<ScrollIcon className="size-5" />}
          title="Agent Logs"
          description="Container stdout/stderr from agent workload pods. Filter by app, workload, or pod."
        />
      )}
      {ZENTINELLE_TABS.has(tab) && <ZentinelleGate tab={tab} />}
    </PageShell>
  );
}
