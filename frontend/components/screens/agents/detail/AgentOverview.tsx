"use client";

import {
  ActivityIcon,
  BookOpenIcon,
  ChevronRightIcon,
  Loader2Icon,
  WrenchIcon,
  ZapIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";

import {
  AgentActivityGraph,
  type AgentActivityTool,
} from "@/components/observability/AgentActivityGraph";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { formatRelativeAge } from "@/lib/format";

import type { AgentOverviewProps } from "./use-agent-overview";

type Dot = "ok" | "warn" | "error" | "muted" | "pending";
const RUN_STATUS_DOT: Record<string, Dot> = {
  running: "pending",
  queued: "warn",
  pending: "warn",
  completed: "ok",
  succeeded: "ok",
  failed: "error",
  timed_out: "error",
  cancelled: "muted",
  canceled: "muted",
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

/**
 * Agent-native Overview — the landing pillar for `/agents/[agentSlug]`.
 *
 * Unlike the app-cloned pillars, this is designed around what an agent *is*:
 * a live activity graph of the agent and its tools (#1091 viz), a one-click
 * dispatch, a recent-runs rollup, and a compact capability summary. It
 * deliberately drops app chrome (URLs, domains, public endpoints) — an agent
 * has none. The deeper Build / Run / Observe / Control / Secure tabs remain
 * for the full detail.
 */
export function AgentOverviewView({
  agent,
  detail,
  detailLoading,
  tasks,
  dispatching,
  sendingInput,
  onDispatch,
  onSendInput,
}: AgentOverviewProps) {
  const skills = React.useMemo(() => detail?.skills ?? [], [detail]);

  // Flatten the agent's bound skills into a de-duped tool list for the graph.
  const tools = React.useMemo<AgentActivityTool[]>(() => {
    const seen = new Set<string>();
    const out: AgentActivityTool[] = [];
    for (const binding of skills) {
      for (const tool of binding.toolDefs) {
        if (seen.has(tool.id)) continue;
        seen.add(tool.id);
        out.push({ id: tool.id, name: tool.name });
      }
    }
    return out;
  }, [skills]);

  const rows = React.useMemo(
    () => [...tasks].sort((a, b) => (b.createdAt ?? "").localeCompare(a.createdAt ?? "")),
    [tasks]
  );

  const counts = React.useMemo(() => {
    const c = { completed: 0, failed: 0, active: 0 };
    for (const t of rows) {
      const s = t.status.toLowerCase();
      if (s === "completed" || s === "succeeded") c.completed += 1;
      else if (s === "failed" || s === "timed_out") c.failed += 1;
      else if (s === "running" || s === "queued") c.active += 1;
    }
    return c;
  }, [rows]);

  const liveRunning = Math.max(agent.runningCount, counts.active);
  const active = liveRunning > 0;
  const runningTask = rows.find((task) => task.status === "running") ?? null;

  return (
    <div className="space-y-6">
      {/* Live activity — the agent + its tools, animated while running. */}
      <Card className="overflow-hidden">
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2 text-base">
            <ActivityIcon className="size-4" />
            Live activity
            <Badge variant={active ? "default" : "outline"} className="gap-1.5">
              <StatusDot status={active ? "pending" : "muted"} />
              {active ? `${liveRunning} running` : "Idle"}
            </Badge>
            <span className="text-muted-foreground text-xs font-normal">
              {tools.length} {tools.length === 1 ? "tool" : "tools"}
            </span>
            <div className="ml-auto">
              <Button size="sm" onClick={onDispatch} disabled={dispatching}>
                {dispatching ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <ZapIcon className="size-4" />
                )}
                Run once
              </Button>
            </div>
          </CardTitle>
        </CardHeader>
        <CardContent className="px-0 pb-0">
          {detailLoading && tools.length === 0 ? (
            <div className="p-4">
              <Skeleton className="h-[288px] w-full rounded-lg" />
            </div>
          ) : (
            <AgentActivityGraph
              agentName={agent.name}
              tools={tools}
              active={active}
              runningCount={liveRunning}
              className="rounded-none"
            />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            Runtime health
            <Badge variant={detail?.imageRef ? "default" : "outline"} className="ml-auto gap-1.5">
              <StatusDot status={detail?.imageRef ? "ok" : "muted"} />
              {detail?.imageRef ? "Configured" : "Not configured"}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 text-sm sm:grid-cols-3">
          <SummaryTile
            icon={<WrenchIcon className="size-4" />}
            label="Image"
            value={detail?.imageRef || "No image selected"}
            muted={!detail?.imageRef}
          />
          <SummaryTile
            icon={<ActivityIcon className="size-4" />}
            label="Run mode"
            value={`${titleCase(agent.runFamily)} · ${titleCase(agent.runMode)}`}
          />
          <SummaryTile
            icon={<StatusDot status={active ? "pending" : "muted"} />}
            label="Liveness"
            value={active ? `${liveRunning} task${liveRunning === 1 ? "" : "s"} running` : "Idle"}
          />
        </CardContent>
      </Card>

      {/* Recent runs rollup + latest executions. */}
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2 text-base">
            Recent runs
            {rows.length > 0 && (
              <span className="flex flex-wrap items-center gap-3 text-xs font-normal tabular-nums">
                <span className="text-success-fg">{counts.completed} completed</span>
                <span className="text-danger-fg">{counts.failed} failed</span>
                <span className="text-info-fg">{counts.active} active</span>
              </span>
            )}
            <Link
              href={`/agents/${encodeURIComponent(agent.slug)}/run`}
              className="text-muted-foreground hover:text-foreground ml-auto inline-flex items-center text-xs font-normal"
            >
              All executions <ChevronRightIcon className="size-3.5" />
            </Link>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {rows.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              This agent hasn&rsquo;t run yet. Use <span className="font-medium">Run once</span> to
              dispatch it — the run appears here with a live status.
            </p>
          ) : (
            <ul className="divide-y">
              {rows.slice(0, 5).map((t) => {
                const dot = RUN_STATUS_DOT[t.status.toLowerCase()] ?? "muted";
                return (
                  <li key={t.id}>
                    <Link
                      href={`/agents/runs/${encodeURIComponent(t.id)}`}
                      className="hover:bg-muted/40 -mx-2 flex items-center gap-3 rounded-md px-2 py-2"
                    >
                      <StatusDot status={dot} />
                      <span className="font-mono text-xs">{t.id.slice(0, 8)}</span>
                      <Badge
                        variant={dot === "error" ? "destructive" : "secondary"}
                        className="text-xs"
                      >
                        {titleCase(t.status)}
                      </Badge>
                      <span className="text-muted-foreground ml-auto text-xs">
                        {t.startedAt ? formatRelativeAge(t.startedAt) : "—"}
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>

      <OverseerChat
        taskId={runningTask?.id ?? null}
        onSendInput={onSendInput}
        sending={sendingInput}
      />

      {/* Capability summary — brief + skills/tools at a glance, deep detail on Build. */}
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2 text-base">
            Capabilities
            <Link
              href={`/agents/${encodeURIComponent(agent.slug)}/build`}
              className="text-muted-foreground hover:text-foreground ml-auto inline-flex items-center text-xs font-normal"
            >
              Build detail <ChevronRightIcon className="size-3.5" />
            </Link>
          </CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <SummaryTile
            icon={<BookOpenIcon className="size-4" />}
            label="Brief"
            value={detail?.brief ? "Assembled" : "None yet"}
            muted={!detail?.brief}
          />
          <SummaryTile
            icon={<WrenchIcon className="size-4" />}
            label="Skills"
            value={String(skills.length)}
            muted={skills.length === 0}
          />
          <SummaryTile
            icon={<WrenchIcon className="size-4" />}
            label="Tools"
            value={String(tools.length)}
            muted={tools.length === 0}
          />
        </CardContent>
      </Card>
    </div>
  );
}

function OverseerChat({
  taskId,
  onSendInput,
  sending,
}: {
  taskId: string | null;
  onSendInput: AgentOverviewProps["onSendInput"];
  sending: boolean;
}) {
  const [message, setMessage] = React.useState("");
  const [sent, setSent] = React.useState<string[]>([]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const value = message.trim();
    if (!taskId || !value || sending) return;
    if (await onSendInput(taskId, value)) {
      setSent((prior) => [...prior, value]);
      setMessage("");
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <ActivityIcon className="size-4" /> Overseer chat
          <Badge variant={taskId ? "default" : "outline"} className="ml-auto">
            {taskId ? "Connected to running task" : "Available when running"}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {sent.length > 0 && (
          <div className="mb-3 space-y-2">
            {sent.map((entry, index) => (
              <div key={`${entry}-${index}`} className="bg-muted/40 rounded-md px-3 py-2 text-sm">
                {entry}
              </div>
            ))}
          </div>
        )}
        <form onSubmit={submit} className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <Textarea
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder={
              taskId
                ? "Send a follow-up to the running agent…"
                : "Start a run to enable overseer chat"
            }
            disabled={!taskId || sending}
            rows={2}
            aria-label="Overseer message"
          />
          <Button type="submit" disabled={!taskId || !message.trim() || sending}>
            {sending ? <Loader2Icon className="size-4 animate-spin" /> : "Send"}
          </Button>
        </form>
        <p className="text-muted-foreground mt-2 text-xs">
          Messages are queued and delivered at the agent’s next turn boundary.
        </p>
      </CardContent>
    </Card>
  );
}

function SummaryTile({
  icon,
  label,
  value,
  muted,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  muted?: boolean;
}) {
  return (
    <div className="bg-muted/30 flex items-center gap-3 rounded-lg border p-3">
      <span className="text-muted-foreground">{icon}</span>
      <div className="flex flex-col">
        <span className="text-muted-foreground text-xs tracking-wide uppercase">{label}</span>
        <span className={muted ? "text-muted-foreground text-sm" : "text-sm font-medium"}>
          {value}
        </span>
      </div>
    </div>
  );
}
