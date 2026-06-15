"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@apollo/client/react";
import {
  BarChart3Icon,
  BotIcon,
  BrainIcon,
  ExternalLinkIcon,
  LayersIcon,
  MonitorPlayIcon,
  ScrollIcon,
  ShieldCheckIcon,
  ZapIcon,
} from "lucide-react";
import Link from "next/link";

import { AgentTheatre } from "@/components/observability/AgentTheatre";
import { EmptyState } from "@/components/EmptyState";
import { ListControls, SortableHeader } from "@/components/ListControls";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { useListControls } from "@/hooks/use-list-controls";
import { LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

type Tab =
  | "fleet"
  | "theatre"
  | "metrics"
  | "logs"
  | "activity"
  | "reasoning"
  | "token-usage"
  | "compliance";
const TABS: readonly Tab[] = [
  "fleet",
  "theatre",
  "metrics",
  "logs",
  "activity",
  "reasoning",
  "token-usage",
  "compliance",
];
const TAB_LABELS: Record<Tab, string> = {
  fleet: "Fleet",
  theatre: "Theatre",
  metrics: "Metrics",
  logs: "Logs",
  activity: "Activity",
  reasoning: "Reasoning Traces",
  "token-usage": "Token Usage",
  compliance: "Compliance",
};
const TAB_ICONS: Record<Tab, React.ReactNode> = {
  fleet: <LayersIcon className="size-4" />,
  theatre: <MonitorPlayIcon className="size-4" />,
  metrics: <BarChart3Icon className="size-4" />,
  logs: <ScrollIcon className="size-4" />,
  activity: <ShieldCheckIcon className="size-4" />,
  reasoning: <BrainIcon className="size-4" />,
  "token-usage": <ZapIcon className="size-4" />,
  compliance: <ShieldCheckIcon className="size-4" />,
};
const ZENTINELLE_TABS = new Set<Tab>(["activity", "reasoning", "token-usage", "compliance"]);

function FleetTab() {
  const { data, loading } = useQuery<{ astroliftWorkloads: AstroliftWorkload[] }>(LIST_WORKLOADS, {
    variables: {},
    fetchPolicy: "cache-and-network",
  });
  const workloads = (data?.astroliftWorkloads ?? []).filter((w) => w.kind === "agent");

  const ctrl = useListControls({
    data: workloads,
    searchFn: (w) => [w.name, w.registeredAppSlug].join(" "),
    sortFn: (a, b, sort) => {
      if (sort.key === "name") return a.name.localeCompare(b.name);
      if (sort.key === "app") return a.registeredAppSlug.localeCompare(b.registeredAppSlug);
      return 0;
    },
    initialPageSize: 25,
  });

  if (loading && workloads.length === 0)
    return <div className="space-y-2"><Skeleton className="h-12 w-full" /><Skeleton className="h-12 w-full" /></div>;

  if (workloads.length === 0)
    return (
      <EmptyState icon={<BotIcon className="size-5" />} title="No agent workloads"
        description="Declare a workload with kind: agent in your app manifest to register it here."
        actionHref="/apps" actionLabel="Browse apps" />
    );

  return (
    <>
      <ListControls controls={ctrl} searchPlaceholder="Filter workloads..." />
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>
              <SortableHeader sortKey="name" sort={ctrl.sort} onToggle={ctrl.toggleSort}>Workload</SortableHeader>
            </TableHead>
            <TableHead>
              <SortableHeader sortKey="app" sort={ctrl.sort} onToggle={ctrl.toggleSort}>App</SortableHeader>
            </TableHead>
            <TableHead>Replicas</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {ctrl.rows.map((w) => (
            <TableRow key={w.id}>
              <TableCell className="font-medium">
                <Link href={`/apps/${w.registeredAppSlug}/workloads`} className="hover:underline">{w.name}</Link>
              </TableCell>
              <TableCell><Badge variant="outline">{w.registeredAppSlug}</Badge></TableCell>
              <TableCell className="text-muted-foreground text-sm">{w.replicas}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </>
  );
}

function ZentinelleGate({ tab }: { tab: Tab }) {
  const descriptions: Record<string, string> = {
    activity: "Policy evaluation results, content scans, blocked requests, and real-time agent behavior events — powered by Zentinelle's policy engine.",
    reasoning: "Full interaction audit: prompts, model responses, tool calls, chain-of-thought steps, and retry attempts — from Zentinelle's InteractionLog.",
    "token-usage": "Per-run and per-workload token consumption: input tokens, output tokens, cost attribution, and budget burn rate — from Zentinelle's cost meter.",
    compliance: "SOC2, GDPR, HIPAA, and EU AI Act controls mapped to this agent workload — from Zentinelle's compliance engine.",
  };
  return (
    <div className="rounded-lg border border-dashed p-8 flex flex-col items-center gap-4 text-center">
      <div className="flex size-12 items-center justify-center rounded-full bg-muted">{TAB_ICONS[tab]}</div>
      <div className="space-y-1">
        <p className="font-semibold text-sm">{TAB_LABELS[tab]} — powered by Zentinelle</p>
        <p className="text-muted-foreground text-sm max-w-md">{descriptions[tab as string]}</p>
      </div>
      <p className="text-xs text-muted-foreground border rounded px-3 py-2 bg-muted/40 max-w-sm">
        Connect Zentinelle to enable AI agent GRC observability. Integration under design.
      </p>
      <a href="https://github.com/calliopeai/zentinelle" target="_blank" rel="noopener noreferrer"
        className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground">
        Learn about Zentinelle <ExternalLinkIcon className="size-3" />
      </a>
    </div>
  );
}

export default function ObserveAgentsPage() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab") as Tab | null;
  const tab: Tab = rawTab && TABS.includes(rawTab) ? rawTab : "fleet";

  function setTab(next: Tab) {
    const params = new URLSearchParams(searchParams.toString());
    if (next === "fleet") params.delete("tab");
    else params.set("tab", next);
    router.replace(`${pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  return (
    <PageShell title="Observe · Agents" description="Fleet of agent workloads and AI behavior observability.">
      <div className="flex gap-0.5 border-b pb-0 mb-4 overflow-x-auto">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={["flex items-center gap-1.5 px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors whitespace-nowrap shrink-0",
              t === tab ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"].join(" ")}>
            {TAB_ICONS[t]}
            {TAB_LABELS[t]}
            {ZENTINELLE_TABS.has(t) && (
              <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-4 font-normal ml-0.5">Zentinelle</Badge>
            )}
          </button>
        ))}
      </div>
      {tab === "fleet" ? <FleetTab /> :
       tab === "theatre" ? <AgentTheatre /> :
       tab === "metrics" ? <EmptyState icon={<BarChart3Icon className="size-5" />} title="Agent Metrics" description="Dispatch rate, run duration (p50/p95), retry rate, and success counts — aggregated across all agent workloads." /> :
       tab === "logs" ? <EmptyState icon={<ScrollIcon className="size-5" />} title="Agent Logs" description="Container stdout/stderr from agent workload pods. Filter by app, workload, or pod." /> :
       <ZentinelleGate tab={tab} />}
    </PageShell>
  );
}
