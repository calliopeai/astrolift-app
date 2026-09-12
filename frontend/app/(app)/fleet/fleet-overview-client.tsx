"use client";

import { useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  BotIcon,
  CircleDotIcon,
  Clock3Icon,
  CpuIcon,
  ListChecksIcon,
  RadioTowerIcon,
  RefreshCwIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Section } from "@/components/ui/section";
import { StatTile } from "@/components/ui/stat-tile";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  LIST_AGENT_ENVIRONMENT_SPECS,
  LIST_AGENT_FLEET,
  LIST_AGENT_TASKS,
} from "@/graphql/agents/agents.queries";
import type {
  AstroliftAgentEnvironmentSpec,
  AstroliftAgentListItem,
  AstroliftAgentTask,
} from "@/graphql/agents/agents.types";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

import { FleetMapClient } from "./map/fleet-map-client";

type FleetData = { agentFleet: AstroliftAgentListItem[] };
type TaskData = { agentTasks: AstroliftAgentTask[] };
type RuntimeData = { agentEnvironmentSpecs: AstroliftAgentEnvironmentSpec[] };

const ACTIVE = new Set(["queued", "provisioning", "running"]);
const FAILING = new Set(["failed", "timed_out"]);

function age(iso: string | null): string {
  if (!iso) return "—";
  const ms = Date.now() - Date.parse(iso);
  if (!Number.isFinite(ms) || ms < 0) return "just now";
  const minutes = Math.floor(ms / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function statusTone(status: string): "default" | "secondary" | "destructive" | "outline" {
  if (status === "running") return "default";
  if (FAILING.has(status)) return "destructive";
  if (ACTIVE.has(status)) return "secondary";
  return "outline";
}

export function FleetOverviewClient() {
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";
  const fleet = useQuery<FleetData>(LIST_AGENT_FLEET, {
    variables: { orgId },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
    pollInterval: 10000,
  });
  const tasks = useQuery<TaskData>(LIST_AGENT_TASKS, {
    variables: { orgId, status: null, workloadId: null },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
    pollInterval: 5000,
  });
  const runtimes = useQuery<RuntimeData>(LIST_AGENT_ENVIRONMENT_SPECS, {
    variables: { orgId },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
  });

  const agents = fleet.data?.agentFleet ?? [];
  const rows = React.useMemo(
    () =>
      [...(tasks.data?.agentTasks ?? [])].sort(
        (a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt)
      ),
    [tasks.data?.agentTasks]
  );
  const activeTasks = rows.filter((task) => ACTIVE.has(task.status));
  const incidents = rows.filter((task) => FAILING.has(task.status));
  const runningAgents = agents.filter((agent) => agent.runningCount > 0).length;
  const loading = fleet.loading || tasks.loading || runtimes.loading;

  return (
    <PageShell
      title="Fleet overview"
      description="A live command center for agents, runtimes, dispatch, and task health."
      actions={
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            void Promise.all([fleet.refetch(), tasks.refetch(), runtimes.refetch()]);
          }}
        >
          <RefreshCwIcon className="mr-2 size-4" /> Refresh
        </Button>
      }
    >
      <Section title="At a glance" description="Current state reported by the Dispatch service.">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatTile
            label="Agents"
            value={agents.length}
            icon={BotIcon}
            loading={loading}
            href="/agents?tab=registry"
            footer={`${runningAgents} active now`}
          />
          <StatTile
            label="Runtimes"
            value={runtimes.data?.agentEnvironmentSpecs.length}
            icon={CpuIcon}
            loading={loading}
            href="/agents?tab=boxes"
            footer="Available environment specs"
          />
          <StatTile
            label="Active tasks"
            value={activeTasks.length}
            icon={ListChecksIcon}
            loading={loading}
            href="/agents?tab=active"
            footer="Queued, provisioning, or running"
          />
          <StatTile
            label="Incidents"
            value={incidents.length}
            icon={AlertTriangleIcon}
            loading={loading}
            href="/agents?tab=history"
            footer="Failed or timed out tasks"
          />
        </div>
      </Section>

      <Section
        title="Fleet topology"
        description="Dispatchers route work through the live runtime fleet."
      >
        <Card className="overflow-hidden">
          <FleetMapClient />
        </Card>
      </Section>

      <div className="grid gap-6 xl:grid-cols-[1.35fr_1fr]">
        <Section title="Recent activity" description="The latest task transitions from Dispatch.">
          <Card>
            <CardContent className="p-0">
              {loading && rows.length === 0 ? (
                <div className="space-y-3 p-5">
                  <Skeleton className="h-10 w-full" />
                  <Skeleton className="h-10 w-full" />
                  <Skeleton className="h-10 w-full" />
                </div>
              ) : rows.length === 0 ? (
                <EmptyState
                  icon={<Clock3Icon className="size-5" />}
                  title="No task activity yet"
                  description="Tasks dispatched through this organization will appear here."
                  actionHref="/agents?tab=dispatch"
                  actionLabel="Open dispatch"
                />
              ) : (
                <ul className="divide-y">
                  {rows.slice(0, 10).map((task) => (
                    <li key={task.id} className="flex items-center justify-between gap-4 px-5 py-3">
                      <div className="min-w-0">
                        <Link
                          href={`/agents/runs/${encodeURIComponent(task.id)}`}
                          className="truncate font-medium hover:underline"
                        >
                          {task.agentName || task.agentSlug || "Agent task"}
                        </Link>
                        <p className="text-muted-foreground truncate font-mono text-xs">
                          {task.projectSlug || "unassigned"} · {age(task.createdAt)}
                        </p>
                      </div>
                      <Badge variant={statusTone(task.status)} className="shrink-0 capitalize">
                        {task.status.replace(/_/g, " ")}
                      </Badge>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </Section>

        <Section
          title="Runtime health"
          description="Reusable environments currently available to the fleet."
        >
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <RadioTowerIcon className="size-4" /> Environment specs
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              {runtimes.loading ? (
                <div className="space-y-3 px-5 pb-5">
                  <Skeleton className="h-8 w-full" />
                  <Skeleton className="h-8 w-full" />
                </div>
              ) : runtimes.data?.agentEnvironmentSpecs.length ? (
                <ul className="divide-y">
                  {runtimes.data.agentEnvironmentSpecs.slice(0, 8).map((runtime) => (
                    <li
                      key={runtime.id}
                      className="flex items-center justify-between gap-3 px-5 py-3"
                    >
                      <div className="min-w-0">
                        <p className="truncate font-medium">{runtime.name}</p>
                        <p className="text-muted-foreground truncate font-mono text-xs">
                          {runtime.runtime} · {runtime.agentType}
                        </p>
                      </div>
                      <CircleDotIcon
                        className="text-success size-4 shrink-0"
                        aria-label="Available"
                      />
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState
                  icon={<CpuIcon className="size-5" />}
                  title="No runtimes configured"
                  description="Add an environment spec before dispatching an agent."
                  actionHref="/agents?tab=boxes"
                  actionLabel="Manage runtimes"
                />
              )}
            </CardContent>
          </Card>
        </Section>
      </div>

      <Section
        title="Agent roster"
        description="Registered agents and the runtime work they are carrying now."
      >
        <Card>
          <CardContent className="p-0">
            {agents.length === 0 ? (
              <EmptyState
                icon={<BotIcon className="size-5" />}
                title="No agents registered"
                description="Register an agent repository before dispatching work."
                actionHref="/agents?tab=registry"
                actionLabel="Open agent registry"
              />
            ) : (
              <ul className="grid divide-y sm:grid-cols-2 sm:divide-x sm:divide-y-0">
                {agents.slice(0, 12).map((agent) => (
                  <li key={agent.id} className="flex items-center justify-between gap-4 px-5 py-4">
                    <div className="min-w-0">
                      <Link
                        href={`/agents/${encodeURIComponent(agent.slug)}`}
                        className="truncate font-medium hover:underline"
                      >
                        {agent.name}
                      </Link>
                      <p className="text-muted-foreground truncate font-mono text-xs">
                        {agent.projectSlug}/{agent.slug}
                      </p>
                    </div>
                    <Badge variant={agent.runningCount > 0 ? "default" : "outline"}>
                      {agent.runningCount > 0 ? `${agent.runningCount} running` : "Idle"}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </Section>
    </PageShell>
  );
}
