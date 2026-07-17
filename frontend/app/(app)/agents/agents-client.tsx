"use client";

import { useQuery } from "@apollo/client/react";
import {
  BarChart3Icon,
  BotIcon,
  BrainIcon,
  ClockIcon,
  ExternalLinkIcon,
  GitBranchIcon,
  MonitorPlayIcon,
  ScrollIcon,
  ShieldCheckIcon,
  ZapIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { ListControls, SortableHeader } from "@/components/ListControls";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { AgentTheatre } from "@/components/observability/AgentTheatre";
import { VncViewer } from "@/components/observability/VncViewer";
import {
  LIST_AGENT_FLEET,
  LIST_AGENT_LIVE_STATUS,
  LIST_AGENT_TASKS,
  LIST_AGENT_WORKLOADS,
} from "@/graphql/agents/agents.queries";
import type {
  AstroliftAgentListItem,
  AstroliftAgentLiveStatus,
} from "@/graphql/agents/agents.types";
import { LIST_PROJECTS } from "@/graphql/identity/identity.queries";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { DispatchTab } from "./dispatch-tab";
import { useModules } from "@/graphql/user/user.hooks";
import { FEATURE_FLAG_ZENTINELLE, useFeatureFlag } from "@/graphql/server/server.hooks";
import { formatRelativeAge } from "@/lib/format";
import { useListControls, type SortState } from "@/hooks/use-list-controls";

type AgentTask = {
  id: string;
  status: string;
  callbackUrl: string;
  result: unknown;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  vncEnabled: boolean;
  vncUrl: string;
};

type AgentTasksData = {
  agentTasks: AgentTask[];
};

type AgentTab =
  | "active"
  | "dispatch"
  | "history"
  | "registry"
  | "theatre"
  | "metrics"
  | "logs"
  | "activity"
  | "reasoning"
  | "token-usage"
  | "compliance";
const AGENT_TABS: readonly AgentTab[] = [
  "active",
  "dispatch",
  "history",
  "registry",
  "theatre",
  "metrics",
  "logs",
  "activity",
  "reasoning",
  "token-usage",
  "compliance",
];

const TAB_LABELS: Record<AgentTab, string> = {
  active: "Active",
  dispatch: "Dispatch",
  history: "History",
  registry: "Registry",
  theatre: "Theatre",
  metrics: "Metrics",
  logs: "Logs",
  activity: "Activity",
  reasoning: "Reasoning Traces",
  "token-usage": "Token Usage",
  compliance: "Compliance",
};

const TAB_ICONS: Partial<Record<AgentTab, React.ReactNode>> = {
  theatre: <MonitorPlayIcon className="size-4" />,
  metrics: <BarChart3Icon className="size-4" />,
  logs: <ScrollIcon className="size-4" />,
  activity: <ShieldCheckIcon className="size-4" />,
  reasoning: <BrainIcon className="size-4" />,
  "token-usage": <ZapIcon className="size-4" />,
  compliance: <ShieldCheckIcon className="size-4" />,
};

const ZENTINELLE_TABS = new Set<AgentTab>([
  "activity",
  "reasoning",
  "token-usage",
  "compliance",
]);

// ---------------------------------------------------------------------------
// Active tab — running agent tasks
// ---------------------------------------------------------------------------

function ActiveTab({ orgId }: { orgId: string }) {
  const { data, loading } = useQuery<AgentTasksData>(LIST_AGENT_TASKS, {
    variables: { orgId, status: "running" },
    pollInterval: 5000,
    skip: !orgId,
  });
  const tasks = data?.agentTasks ?? [];
  // The task whose live session is open in the theater modal.
  const [watching, setWatching] = React.useState<AgentTask | null>(null);

  // A task is watchable only while RUNNING on a VNC-capable pod with a
  // published relay path.
  const canWatch = (t: AgentTask) =>
    t.status === "running" && t.vncEnabled && Boolean(t.vncUrl);

  if (loading && tasks.length === 0) {
    return (
      <Card>
        <CardContent className="space-y-2 p-6">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (tasks.length === 0) {
    return (
      <Card>
        <CardContent className="p-6">
          <EmptyState
            icon={<BotIcon className="size-5" />}
            title="No active agent tasks"
            description="No agent tasks are currently running. Use the Dispatch tab to launch a task."
          />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>ID</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Started</TableHead>
              <TableHead className="text-right">Live</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {tasks.map((t) => (
              <TableRow key={t.id}>
                <TableCell className="font-mono text-xs">
                  <Link
                    href={`/agents/runs/${encodeURIComponent(t.id)}`}
                    className="hover:text-[var(--brand-primary)] hover:underline"
                  >
                    {t.id}
                  </Link>
                </TableCell>
                <TableCell><Badge variant="default">{t.status}</Badge></TableCell>
                <TableCell className="text-muted-foreground text-sm">{t.startedAt ?? "—"}</TableCell>
                <TableCell className="text-right">
                  {canWatch(t) && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setWatching(t)}
                    >
                      <MonitorPlayIcon className="size-4" />
                      Watch live
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>

      <Dialog open={watching !== null} onOpenChange={(open) => !open && setWatching(null)}>
        <DialogContent className="max-w-4xl sm:max-w-4xl">
          <DialogHeader>
            <DialogTitle>Live agent session</DialogTitle>
            <DialogDescription className="font-mono text-xs">
              {watching?.id}
            </DialogDescription>
          </DialogHeader>
          {watching && <VncViewer vncPath={watching.vncUrl} />}
        </DialogContent>
      </Dialog>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// History tab — completed / terminal agent tasks
// ---------------------------------------------------------------------------

// Terminal AgentTask.Status values (backend contract): there is no
// "succeeded" — a successful task is "completed".
const TERMINAL_STATUSES = ["completed", "failed", "timed_out", "cancelled"];

function HistoryTab({ orgId }: { orgId: string }) {
  const { data, loading } = useQuery<AgentTasksData>(LIST_AGENT_TASKS, {
    variables: { orgId },
    skip: !orgId,
  });
  const tasks = (data?.agentTasks ?? []).filter((t) =>
    TERMINAL_STATUSES.includes(t.status)
  );

  if (loading && tasks.length === 0) {
    return (
      <Card>
        <CardContent className="space-y-2 p-6">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (tasks.length === 0) {
    return (
      <Card>
        <CardContent className="p-6">
          <EmptyState
            icon={<ClockIcon className="size-5" />}
            title="No task history"
            description="No completed agent tasks yet. Tasks will appear here after they finish."
          />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>ID</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Finished</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {tasks.map((t) => (
              <TableRow key={t.id}>
                <TableCell className="font-mono text-xs">
                  <Link
                    href={`/agents/runs/${encodeURIComponent(t.id)}`}
                    className="hover:text-[var(--brand-primary)] hover:underline"
                  >
                    {t.id}
                  </Link>
                </TableCell>
                <TableCell>
                  <Badge variant={t.status === "completed" ? "default" : "destructive"}>
                    {t.status}
                  </Badge>
                </TableCell>
                <TableCell className="text-muted-foreground text-sm">{t.finishedAt ?? "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Registry tab — registered AGENTS (kind=agent Workloads), project-scoped
// with a fleet/all-agents toggle. (spec 33 §3 / PR-7)
//
// This is the *registered-agents* surface, not the runs surface — runs
// live in the Active / History tabs and become the Executions view in a
// later PR. The base list (agentWorkloads / agentFleet) is fetched once;
// the volatile live-status (running count, next-scheduled, paused/idle)
// is a separate 15s-polled query merged into rows by workloadId, matching
// how /jobs polls its run signal.
// ---------------------------------------------------------------------------

// Render runFamily + runMode as a human-readable cell, e.g. "Task · Schedule"
// or "Service". Both are free `String!` fields on the schema; normalize
// case-insensitively and title-case any value we don't recognize so a new
// backend mode degrades gracefully rather than rendering a raw token.
const RUN_FAMILY_LABELS: Record<string, string> = {
  task: "Task",
  service: "Service",
};
const RUN_MODE_LABELS: Record<string, string> = {
  once: "Once",
  loop: "Loop",
  schedule: "Schedule",
  trigger: "Trigger",
  service: "Service",
};

function titleCase(value: string): string {
  if (!value) return "";
  return value
    .replace(/[_-]+/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

function formatRunMode(runFamily: string, runMode: string): string {
  const family = RUN_FAMILY_LABELS[runFamily.toLowerCase()] ?? titleCase(runFamily);
  const mode = RUN_MODE_LABELS[runMode.toLowerCase()] ?? titleCase(runMode);
  // Service is single-axis (the family *is* the mode); Task carries a
  // sub-mode (Once/Loop/Schedule/Trigger) so we show "Family · Mode".
  if (!mode || mode === family) return family || "—";
  return `${family} · ${mode}`;
}

// Last-run status → {dot, badge-variant}. Agent run vocabulary (spec 33 §6):
// running / queued / completed / failed / timed_out / cancelled. Mirrors the
// dot+badge convention the platform uses elsewhere (RunStatusBadge) but with
// the agent-run vocabulary, which differs from ScheduledJobRun ("completed"
// not "succeeded").
type Dot = "ok" | "warn" | "error" | "muted" | "pending";
const RUN_STATUS_DOT: Record<string, Dot> = {
  running: "pending",
  queued: "warn",
  completed: "ok",
  succeeded: "ok",
  failed: "error",
  timed_out: "error",
  cancelled: "muted",
  canceled: "muted",
};

function LastRunCell({
  status,
  at,
}: {
  status: string | null | undefined;
  at: string | null | undefined;
}) {
  if (!status && !at) {
    return <span className="text-muted-foreground text-sm">Never run</span>;
  }
  const key = (status ?? "").toLowerCase();
  const dot = RUN_STATUS_DOT[key] ?? "muted";
  return (
    <span className="inline-flex items-center gap-1.5">
      {status && (
        <>
          <StatusDot status={dot} />
          <Badge
            variant={dot === "error" ? "destructive" : "secondary"}
            className="capitalize"
          >
            {titleCase(status)}
          </Badge>
        </>
      )}
      {at && (
        <span className="text-muted-foreground text-xs" title={at}>
          {formatRelativeAge(at)}
        </span>
      )}
    </span>
  );
}

// Live-status cell: running count + a running / scheduled / paused / idle
// badge. Derived from the polled agentLiveStatus row (falls back to the
// list row's own runningCount/runPaused when live data hasn't arrived yet).
function LiveStatusCell({
  live,
  fallback,
}: {
  live: AstroliftAgentLiveStatus | undefined;
  fallback: Pick<AstroliftAgentListItem, "runningCount" | "runPaused">;
}) {
  const runningCount = live?.runningCount ?? fallback.runningCount;
  const isPaused = live?.isPaused ?? fallback.runPaused;
  const isIdle = live?.isIdle ?? runningCount === 0;
  const nextScheduledAt = live?.nextScheduledAt ?? null;

  if (runningCount > 0) {
    return (
      <span className="inline-flex items-center gap-1.5">
        <StatusDot status="pending" />
        <Badge variant="default">
          {runningCount} running
        </Badge>
      </span>
    );
  }
  if (isPaused) {
    return (
      <span className="inline-flex items-center gap-1.5">
        <StatusDot status="muted" />
        <Badge variant="outline">Paused</Badge>
      </span>
    );
  }
  if (nextScheduledAt) {
    return (
      <span className="inline-flex items-center gap-1.5">
        <StatusDot status="warn" />
        <Badge variant="secondary" title={nextScheduledAt}>
          Next {formatRelativeAge(nextScheduledAt)}
        </Badge>
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5">
      <StatusDot status="muted" />
      <Badge variant="outline">{isIdle ? "Idle" : "—"}</Badge>
    </span>
  );
}

function agentSortFn(
  a: AstroliftAgentListItem,
  b: AstroliftAgentListItem,
  sort: SortState
): number {
  const dir = sort.dir === "asc" ? 1 : -1;
  switch (sort.key) {
    case "name":
      return dir * a.name.localeCompare(b.name);
    case "repo":
      return dir * a.sourceRepo.localeCompare(b.sourceRepo);
    case "runMode":
      return (
        dir *
        formatRunMode(a.runFamily, a.runMode).localeCompare(
          formatRunMode(b.runFamily, b.runMode)
        )
      );
    case "lastRun":
      return dir * ((a.lastRunAt ?? "").localeCompare(b.lastRunAt ?? ""));
    default:
      return 0;
  }
}

interface AgentListResp {
  agentWorkloads: AstroliftAgentListItem[];
}
interface AgentFleetResp {
  agentFleet: AstroliftAgentListItem[];
}
interface AgentLiveStatusResp {
  agentLiveStatus: AstroliftAgentLiveStatus[];
}

interface ProjectsResp {
  astroliftProjects: Array<{ id: string; slug: string; name: string }>;
}

interface RegistryTabProps {
  orgId: string;
}

function RegistryTab({ orgId }: RegistryTabProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // The "Register an agent repo" create affordance is gated on the Agents
  // module's server-authoritative `canCreate` (spec 36 §1.3).
  const { canCreate } = useModules();
  const canCreateAgent = canCreate("agents");

  // Project scoping is URL-synced via ?project= (mirrors the Apps list).
  // Empty string === the fleet view (all agents in the org).
  const projectSlug = searchParams.get("project") ?? "";
  const fleet = projectSlug === "";

  const setProjectScope = React.useCallback(
    (next: string) => {
      const params = new URLSearchParams(searchParams.toString());
      if (!next) params.delete("project");
      else params.set("project", next);
      const qs = params.toString();
      router.replace(`${pathname}${qs ? `?${qs}` : ""}`, { scroll: false });
    },
    [pathname, router, searchParams]
  );

  // Projects populate the scope picker. Cheap query; cached across the app.
  const { data: projectsData } = useQuery<ProjectsResp>(LIST_PROJECTS);
  const projects = projectsData?.astroliftProjects ?? [];

  // Base list: fleet vs project-scoped. We run one query and skip the other
  // so we never over-fetch. cache-and-network keeps the list fresh on
  // re-entry without flashing the skeleton.
  const projectQuery = useQuery<AgentListResp>(LIST_AGENT_WORKLOADS, {
    variables: { orgId, projectSlug },
    skip: !orgId || fleet,
    fetchPolicy: "cache-and-network",
  });
  const fleetQuery = useQuery<AgentFleetResp>(LIST_AGENT_FLEET, {
    variables: { orgId },
    skip: !orgId || !fleet,
    fetchPolicy: "cache-and-network",
  });

  const rawAgents: AstroliftAgentListItem[] = React.useMemo(
    () =>
      fleet
        ? fleetQuery.data?.agentFleet ?? []
        : projectQuery.data?.agentWorkloads ?? [],
    [fleet, fleetQuery.data?.agentFleet, projectQuery.data?.agentWorkloads]
  );
  const listLoading = fleet ? fleetQuery.loading : projectQuery.loading;

  // Volatile live-status, polled every 15s (matching /jobs). Scoped the same
  // way as the list; merged into rows by workloadId. No workloadId arg ⇒ the
  // whole scope rolls up in one request.
  const { data: liveData } = useQuery<AgentLiveStatusResp>(LIST_AGENT_LIVE_STATUS, {
    variables: { orgId, projectSlug: fleet ? null : projectSlug, workloadId: null },
    skip: !orgId,
    pollInterval: 15000,
    fetchPolicy: "cache-and-network",
  });
  const liveByWorkloadId = React.useMemo(() => {
    const map = new Map<string, AstroliftAgentLiveStatus>();
    for (const row of liveData?.agentLiveStatus ?? []) {
      map.set(row.workloadId, row);
    }
    return map;
  }, [liveData?.agentLiveStatus]);

  const ctrl = useListControls({
    data: rawAgents,
    searchFn: (a) =>
      [a.name, a.slug, a.appSlug, a.projectSlug, a.sourceRepo, a.runFamily, a.runMode].join(" "),
    initialPageSize: 25,
    initialSort: { key: "name", dir: "asc" },
    sortFn: agentSortFn,
  });

  const scopePicker = (
    <div className="flex flex-wrap items-center gap-2">
      <Label htmlFor="agent-scope" className="text-muted-foreground text-xs">
        Scope
      </Label>
      <Select value={fleet ? "__fleet__" : projectSlug} onValueChange={(v) => setProjectScope(v === "__fleet__" ? "" : v)}>
        <SelectTrigger id="agent-scope" className="h-8 w-56 text-sm">
          <SelectValue placeholder="Select project…" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__fleet__">All agents (fleet)</SelectItem>
          {projects.map((p) => (
            <SelectItem key={p.id} value={p.slug}>
              {p.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );

  if (listLoading && rawAgents.length === 0) {
    return (
      <Card>
        <CardContent className="space-y-3 p-6">
          {scopePicker}
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (rawAgents.length === 0) {
    return (
      <Card>
        <CardContent className="space-y-4 p-6">
          {scopePicker}
          <EmptyState
            icon={<BotIcon className="size-5" />}
            title={fleet ? "No agents registered" : "No agents in this project"}
            description={
              fleet
                ? "Register an agent repo to scan it for agent manifests and add each one as an agent here. Agents share the same image build and deployment pipeline as your apps."
                : "This project has no registered agents yet. Register an agent repo or switch the scope to view agents across the whole fleet."
            }
            actionHref={canCreateAgent ? "/agents/new" : undefined}
            actionLabel={canCreateAgent ? "Register an agent repo" : undefined}
            learnMoreHref="https://github.com/calliopeai/astrolift-docs/blob/main/reference/agents.md"
          />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="space-y-3 p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          {scopePicker}
          <ListControls controls={ctrl} searchPlaceholder="Filter agents…" />
        </div>
        <div className="overflow-x-auto rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>
                  <SortableHeader sortKey="name" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                    Name
                  </SortableHeader>
                </TableHead>
                <TableHead>
                  <SortableHeader sortKey="repo" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                    Repo
                  </SortableHeader>
                </TableHead>
                <TableHead>
                  <SortableHeader sortKey="runMode" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                    Run mode
                  </SortableHeader>
                </TableHead>
                <TableHead>Live status</TableHead>
                <TableHead>
                  <SortableHeader sortKey="lastRun" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                    Last run
                  </SortableHeader>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {ctrl.rows.map((a) => (
                <TableRow key={a.id}>
                  <TableCell>
                    <Link
                      href={`/agents/${encodeURIComponent(a.slug)}/build`}
                      className="font-medium hover:text-[var(--brand-primary)] hover:underline"
                    >
                      {a.name}
                    </Link>
                    <div className="text-muted-foreground font-mono text-xs">
                      {a.projectSlug}/{a.appSlug}/{a.slug}
                    </div>
                  </TableCell>
                  <TableCell>
                    {a.sourceRepo ? (
                      a.sourceUrl ? (
                        <a
                          href={a.sourceUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-sm"
                        >
                          <GitBranchIcon className="size-3.5" />
                          {a.sourceRepo}
                        </a>
                      ) : (
                        <span className="text-muted-foreground inline-flex items-center gap-1 text-sm">
                          <GitBranchIcon className="size-3.5" />
                          {a.sourceRepo}
                        </span>
                      )
                    ) : (
                      <span className="text-muted-foreground text-sm">—</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">{formatRunMode(a.runFamily, a.runMode)}</Badge>
                  </TableCell>
                  <TableCell>
                    <LiveStatusCell
                      live={liveByWorkloadId.get(a.id)}
                      fallback={{ runningCount: a.runningCount, runPaused: a.runPaused }}
                    />
                  </TableCell>
                  <TableCell>
                    <LastRunCell status={a.lastRunStatus} at={a.lastRunAt} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Zentinelle-gated signal tabs
// ---------------------------------------------------------------------------

function ZentinelleGate({ tab }: { tab: AgentTab }) {
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

// ---------------------------------------------------------------------------
// Root client component
// ---------------------------------------------------------------------------

export function AgentsClient() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // Zentinelle governance surfaces (Activity / Reasoning / Token-Usage /
  // Compliance) ship in the codebase but stay hidden unless the install
  // enables them via the `zentinelle.enabled` server-info flag (#1104).
  const zentinelleEnabled = useFeatureFlag(FEATURE_FLAG_ZENTINELLE);
  const visibleTabs = React.useMemo(
    () =>
      zentinelleEnabled
        ? AGENT_TABS
        : AGENT_TABS.filter((tabKey) => !ZENTINELLE_TABS.has(tabKey)),
    [zentinelleEnabled],
  );

  const rawTab = searchParams.get("tab") as AgentTab | null;
  // A ?tab=compliance deep-link while Zentinelle is disabled falls back
  // to the default tab rather than rendering a dead / gated surface.
  const tab: AgentTab = rawTab && visibleTabs.includes(rawTab) ? rawTab : "active";

  function setTab(next: AgentTab) {
    const params = new URLSearchParams(searchParams.toString());
    if (next === "active") {
      params.delete("tab");
    } else {
      params.set("tab", next);
    }
    const qs = params.toString();
    router.replace(`${pathname}${qs ? `?${qs}` : ""}`, { scroll: false });
  }

  // Resolve the active org reactively (not a one-shot cookie read): the
  // cookie is set by useActiveOrg's post-render effect after the org query
  // resolves, so reading it synchronously at first render races and returns
  // "" on a fresh load — leaving every tab's query skipped (skip: !orgId)
  // and the page empty with no re-render to recover. useActiveOrg re-renders
  // when the org loads, so orgId becomes populated and the queries fire.
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";

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
              {ZENTINELLE_TABS.has(tabKey) && (
                <Badge variant="secondary" className="text-2xs px-1.5 py-0 h-4 font-normal ml-0.5">Zentinelle</Badge>
              )}
            </button>
          );
        })}
      </div>

      {tab === "active" && <ActiveTab orgId={orgId} />}
      {tab === "dispatch" && <DispatchTab orgId={orgId} />}
      {tab === "history" && <HistoryTab orgId={orgId} />}
      {tab === "registry" && <RegistryTab orgId={orgId} />}
      {tab === "theatre" && <AgentTheatre />}
      {tab === "metrics" && (
        <EmptyState icon={<BarChart3Icon className="size-5" />} title="Agent Metrics" description="Dispatch rate, run duration (p50/p95), retry rate, and success counts — aggregated across all agent workloads." />
      )}
      {tab === "logs" && (
        <EmptyState icon={<ScrollIcon className="size-5" />} title="Agent Logs" description="Container stdout/stderr from agent workload pods. Filter by app, workload, or pod." />
      )}
      {ZENTINELLE_TABS.has(tab) && <ZentinelleGate tab={tab} />}
    </PageShell>
  );
}
