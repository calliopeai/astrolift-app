"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  BotIcon,
  ClockIcon,
  GitBranchIcon,
  Loader2Icon,
  MonitorPlayIcon,
  ZapIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/EmptyState";
import { ListControls, SortableHeader } from "@/components/ListControls";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { Textarea } from "@/components/ui/textarea";
import { VncViewer } from "@/components/observability/VncViewer";
import { RUN_AGENT } from "@/graphql/agents/agents.mutations";
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
import { LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";
import { formatRelativeAge } from "@/lib/format";
import { getActiveOrgGuid } from "@/lib/identity/active-org";
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

type RunAgentData = {
  runAstroliftAgent: {
    ok: boolean;
    errors: { code: string; message: string; field: string | null }[];
    data: { id: string; status: string; createdAt: string } | null;
  };
};

type AgentTab = "active" | "dispatch" | "history" | "registry";
const AGENT_TABS: readonly AgentTab[] = ["active", "dispatch", "history", "registry"];

const TAB_LABELS: Record<AgentTab, string> = {
  active: "Active",
  dispatch: "Dispatch",
  history: "History",
  registry: "Registry",
};

interface WorkloadResp {
  astroliftWorkloads: AstroliftWorkload[];
}

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
                <TableCell className="font-mono text-xs">{t.id}</TableCell>
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
// Dispatch tab — trigger a new agent run
// ---------------------------------------------------------------------------

interface DispatchTabProps {
  agentWorkloads: AstroliftWorkload[];
  workloadsLoading: boolean;
}

function DispatchTab({ agentWorkloads, workloadsLoading }: DispatchTabProps) {
  const [selectedWorkload, setSelectedWorkload] = React.useState<string>("");
  const [inputJson, setInputJson] = React.useState<string>("");
  const [jsonError, setJsonError] = React.useState<string | null>(null);

  // Refetch the fleet runs list on success so the new run shows on the Active
  // tab without a reload (the per-agent Run tab does its own scoped refetch).
  const [runAgent, { loading: dispatching }] = useMutation<RunAgentData>(RUN_AGENT, {
    refetchQueries: [LIST_AGENT_TASKS],
  });

  function validateJson(value: string): boolean {
    if (!value.trim()) {
      setJsonError(null);
      return true;
    }
    try {
      JSON.parse(value);
      setJsonError(null);
      return true;
    } catch {
      setJsonError("Invalid JSON — check syntax");
      return false;
    }
  }

  async function handleDispatch() {
    if (!selectedWorkload) return;
    if (!validateJson(inputJson)) return;

    const selectedName =
      agentWorkloads.find((w) => w.slug === selectedWorkload)?.name ?? selectedWorkload;
    try {
      const { data } = await runAgent({
        variables: {
          input: {
            agentSlug: selectedWorkload,
            triggerPayload: inputJson.trim() ? JSON.parse(inputJson) : null,
          },
        },
      });
      const result = data?.runAstroliftAgent;
      if (!result?.ok) {
        throw new Error(result?.errors?.[0]?.message ?? "Dispatch failed");
      }
      toast.success(`Dispatched ${selectedName}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(`Couldn't dispatch ${selectedName}`, { description: message });
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Dispatch agent run</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="dispatch-workload">Agent workload</Label>
          {workloadsLoading ? (
            <Skeleton className="h-9 w-full max-w-xs" />
          ) : agentWorkloads.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No agent workloads registered. Declare a workload with{" "}
              <span className="font-mono">kind: agent</span> in your app manifest.
            </p>
          ) : (
            <Select value={selectedWorkload} onValueChange={setSelectedWorkload}>
              <SelectTrigger id="dispatch-workload" className="max-w-xs">
                <SelectValue placeholder="Select workload…" />
              </SelectTrigger>
              <SelectContent>
                {agentWorkloads.map((w) => (
                  <SelectItem key={w.id} value={w.slug}>
                    {w.name} <span className="text-muted-foreground">({w.registeredAppSlug})</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="dispatch-input">Input (JSON, optional)</Label>
          <Textarea
            id="dispatch-input"
            placeholder='{"key": "value"}'
            value={inputJson}
            onChange={(e) => {
              setInputJson(e.target.value);
              if (jsonError) validateJson(e.target.value);
            }}
            onBlur={() => validateJson(inputJson)}
            className="font-mono text-sm"
            rows={5}
          />
          {jsonError && <p className="text-destructive text-xs">{jsonError}</p>}
        </div>

        <div className="pt-1">
          <Button
            disabled={!selectedWorkload || dispatching || agentWorkloads.length === 0}
            onClick={handleDispatch}
          >
            {dispatching && <Loader2Icon className="size-4 animate-spin" />}
            <ZapIcon className="size-4" />
            Dispatch run
          </Button>
          <p className="text-muted-foreground mt-2 text-xs">
            Dispatches the agent for a single run. Track it on the Active tab, or open the
            agent&rsquo;s Run tab for its full execution history.
          </p>
        </div>
      </CardContent>
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
                <TableCell className="font-mono text-xs">{t.id}</TableCell>
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
            actionHref="/agents/new"
            actionLabel="Register an agent repo"
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
// Root client component
// ---------------------------------------------------------------------------

export function AgentsClient() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab") as AgentTab | null;
  const tab: AgentTab = rawTab && AGENT_TABS.includes(rawTab) ? rawTab : "active";

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

  const orgId = getActiveOrgGuid() ?? "";

  // Workloads are loaded here for the Dispatch tab's workload picker.
  // (The Registry tab has its own project-scoped agent queries — PR-7.)
  const { data: workloadsData, loading: workloadsLoading } = useQuery<WorkloadResp>(
    LIST_WORKLOADS,
    { variables: {} }
  );

  const agentWorkloads = React.useMemo(
    () => (workloadsData?.astroliftWorkloads ?? []).filter((w) => w.kind === "agent"),
    [workloadsData?.astroliftWorkloads]
  );

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
        {AGENT_TABS.map((tabKey) => {
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
              {TAB_LABELS[tabKey]}
            </button>
          );
        })}
      </div>

      {tab === "active" && <ActiveTab orgId={orgId} />}
      {tab === "dispatch" && (
        <DispatchTab agentWorkloads={agentWorkloads} workloadsLoading={workloadsLoading} />
      )}
      {tab === "history" && <HistoryTab orgId={orgId} />}
      {tab === "registry" && <RegistryTab orgId={orgId} />}
    </PageShell>
  );
}
