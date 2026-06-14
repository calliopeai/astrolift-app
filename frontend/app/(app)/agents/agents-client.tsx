"use client";

import { useQuery } from "@apollo/client/react";
import {
  BotIcon,
  ClockIcon,
  Loader2Icon,
  MonitorPlayIcon,
  ZapIcon,
} from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
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
import { LIST_AGENT_TASKS } from "@/graphql/agents/agents.queries";
import { LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";
import { getActiveOrgGuid } from "@/lib/identity/active-org";

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
  const [dispatching, setDispatching] = React.useState(false);

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

    setDispatching(true);
    try {
      // TODO(#798): Call runAstroliftAgent mutation once it exists.
      // const { data } = await runAgent({
      //   variables: {
      //     input: {
      //       workloadSlug: selectedWorkload,
      //       input: inputJson.trim() ? JSON.parse(inputJson) : null,
      //     },
      //   },
      // });
      // if (!data?.runAstroliftAgent.ok) {
      //   throw new Error(data?.runAstroliftAgent.errors[0]?.message ?? "Dispatch failed");
      // }
      // toast.success("Agent run dispatched");
      throw new Error("runAstroliftAgent mutation not yet implemented — see #798");
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      // eslint-disable-next-line no-console
      console.error("dispatch error:", message);
    } finally {
      setDispatching(false);
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
            Dispatch is a stub — the{" "}
            <span className="font-mono">runAstroliftAgent</span> mutation will be wired in a
            follow-up (#798).
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// History tab — completed / terminal agent tasks
// ---------------------------------------------------------------------------

const TERMINAL_STATUSES = ["succeeded", "failed", "cancelled"];

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
                  <Badge variant={t.status === "succeeded" ? "default" : "destructive"}>
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
// Registry tab — agent-kind workloads across all apps
// ---------------------------------------------------------------------------

interface RegistryTabProps {
  agentWorkloads: AstroliftWorkload[];
  workloadsLoading: boolean;
}

function RegistryTab({ agentWorkloads, workloadsLoading }: RegistryTabProps) {
  if (workloadsLoading) {
    return (
      <Card>
        <CardContent className="space-y-2 p-6">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (agentWorkloads.length === 0) {
    return (
      <Card>
        <CardContent className="p-6">
          <EmptyState
            icon={<BotIcon className="size-5" />}
            title="No agent workloads registered"
            description="Declare a workload with kind: agent in your app manifest to register it here. Agents share the same image build and deployment pipeline as other workloads."
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
              <TableHead>Name</TableHead>
              <TableHead>App</TableHead>
              <TableHead>Slug</TableHead>
              <TableHead>CPU</TableHead>
              <TableHead>Memory</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {agentWorkloads.map((w) => (
              <TableRow key={w.id}>
                <TableCell className="font-medium">{w.name}</TableCell>
                <TableCell>
                  <Badge variant="outline">{w.registeredAppSlug}</Badge>
                </TableCell>
                <TableCell className="font-mono text-xs">{w.slug}</TableCell>
                <TableCell className="text-muted-foreground text-sm">
                  {w.cpuRequest || "—"} / {w.cpuLimit || "—"}
                </TableCell>
                <TableCell className="text-muted-foreground text-sm">
                  {w.memoryRequest || "—"} / {w.memoryLimit || "—"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
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

  // Workloads are loaded here so both the Dispatch and Registry tabs
  // can share the single query result.
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
              {/* Registry count badge — shows how many agent workloads are registered */}
              {tabKey === "registry" && !workloadsLoading && agentWorkloads.length > 0 && (
                <span
                  className={
                    "inline-flex min-w-5 items-center justify-center rounded-full px-1.5 text-xs tabular-nums " +
                    (active ? "bg-muted text-foreground" : "bg-muted/70 text-muted-foreground")
                  }
                >
                  {agentWorkloads.length}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {tab === "active" && <ActiveTab orgId={orgId} />}
      {tab === "dispatch" && (
        <DispatchTab agentWorkloads={agentWorkloads} workloadsLoading={workloadsLoading} />
      )}
      {tab === "history" && <HistoryTab orgId={orgId} />}
      {tab === "registry" && (
        <RegistryTab agentWorkloads={agentWorkloads} workloadsLoading={workloadsLoading} />
      )}
    </PageShell>
  );
}
