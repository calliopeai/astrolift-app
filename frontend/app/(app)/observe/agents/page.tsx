"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  BarChart3Icon,
  BrainIcon,
  ExternalLinkIcon,
  ScrollIcon,
  ShieldCheckIcon,
  ZapIcon,
} from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";

// ---------------------------------------------------------------------------
// Tab definitions — two ownership tiers:
//   ASTROLIFT  = infrastructure observability (pod health, container logs)
//   ZENTINELLE = AI behavior observability (policy evals, tokens, audit)
//
// Zentinelle tabs show an integration prompt until Zentinelle is wired.
// ---------------------------------------------------------------------------

type Tab = "metrics" | "logs" | "activity" | "reasoning" | "token-usage" | "compliance";

interface TabDef {
  id: Tab;
  label: string;
  icon: React.ReactNode;
  owner: "astrolift" | "zentinelle";
  description: string;
}

const TABS: readonly TabDef[] = [
  {
    id: "metrics",
    label: "Metrics",
    icon: <BarChart3Icon className="size-4" />,
    owner: "astrolift",
    description:
      "Pod CPU and memory usage, replica health, restart counts, and deployment rollout status for agent workloads — sourced from the K8s metrics-server via the connected cluster.",
  },
  {
    id: "logs",
    label: "Logs",
    icon: <ScrollIcon className="size-4" />,
    owner: "astrolift",
    description:
      "Container stdout/stderr from agent workload pods. Secrets are redacted at ingest. Filter by app, workload, or pod. Full log stream available from the app Console tab.",
  },
  {
    id: "activity",
    label: "Activity",
    icon: <ShieldCheckIcon className="size-4" />,
    owner: "zentinelle",
    description:
      "Policy evaluation results, content scans, blocked requests, and real-time agent behavior events — powered by Zentinelle's policy engine and content scanner.",
  },
  {
    id: "reasoning",
    label: "Reasoning Traces",
    icon: <BrainIcon className="size-4" />,
    owner: "zentinelle",
    description:
      "Full interaction audit: prompts, model responses, tool calls, chain-of-thought steps, and retry attempts — sourced from Zentinelle's InteractionLog. Required for SOC2 and EU AI Act audit trails.",
  },
  {
    id: "token-usage",
    label: "Token Usage",
    icon: <ZapIcon className="size-4" />,
    owner: "zentinelle",
    description:
      "Per-run and per-workload token consumption: input tokens, output tokens, cost attribution, and budget burn rate — sourced from Zentinelle's cost meter.",
  },
  {
    id: "compliance",
    label: "Compliance",
    icon: <ShieldCheckIcon className="size-4" />,
    owner: "zentinelle",
    description:
      "SOC2, GDPR, HIPAA, and EU AI Act controls mapped to this agent workload. Shows control status, evidence gaps, and last assessment date — sourced from Zentinelle's compliance engine.",
  },
];

const OWNER_BADGE: Record<"astrolift" | "zentinelle", React.ReactNode> = {
  astrolift: (
    <Badge variant="outline" className="text-[10px] px-1.5 py-0 h-4 font-normal">
      Astrolift
    </Badge>
  ),
  zentinelle: (
    <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-4 font-normal">
      Zentinelle
    </Badge>
  ),
};

// ---------------------------------------------------------------------------
// Zentinelle integration stub — shown for Zentinelle-owned tabs
// ---------------------------------------------------------------------------

function ZentinelleGate({ tab }: { tab: TabDef }) {
  return (
    <div className="rounded-lg border border-dashed border-border p-8 flex flex-col items-center gap-4 text-center">
      <div className="flex size-12 items-center justify-center rounded-full bg-muted">
        {tab.icon}
      </div>
      <div className="space-y-1">
        <p className="font-semibold text-sm">{tab.label} — powered by Zentinelle</p>
        <p className="text-muted-foreground text-sm max-w-md">{tab.description}</p>
      </div>
      <p className="text-xs text-muted-foreground border border-border rounded px-3 py-2 bg-muted/40 max-w-sm">
        Connect Zentinelle to this Astrolift install to enable AI agent GRC observability.
        The integration shape is under design — check back soon.
      </p>
      <a
        href="https://github.com/calliopeai/zentinelle"
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        Learn about Zentinelle
        <ExternalLinkIcon className="size-3" />
      </a>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ObserveAgentsPage() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab") as Tab | null;
  const activeTab = TABS.find((t) => t.id === rawTab) ?? TABS[0];

  function setTab(next: Tab) {
    const params = new URLSearchParams(searchParams.toString());
    if (next === "metrics") params.delete("tab");
    else params.set("tab", next);
    router.replace(`${pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  return (
    <PageShell
      title="Observe · Agents"
      description="Infrastructure health and AI behavior observability for agent workloads."
    >
      {/* Tab bar with owner badges */}
      <div className="flex gap-0.5 border-b pb-0 mb-4 overflow-x-auto">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={[
              "flex items-center gap-1.5 px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors whitespace-nowrap shrink-0",
              t.id === activeTab.id
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            ].join(" ")}
          >
            {t.icon}
            {t.label}
            <span className="ml-0.5">{OWNER_BADGE[t.owner]}</span>
          </button>
        ))}
      </div>

      {/* Content */}
      {activeTab.owner === "astrolift" ? (
        <EmptyState
          icon={activeTab.icon}
          title={`Agent ${activeTab.label}`}
          description={activeTab.description}
        />
      ) : (
        <ZentinelleGate tab={activeTab} />
      )}
    </PageShell>
  );
}
