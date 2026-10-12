"use client";

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
import type * as React from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import type { AstroliftAgentListItem, AstroliftAgentTask } from "@/graphql/agents/agents.types";

import { ListSummary } from "@/components/list/ListSummary";
import { PageShell } from "@/components/PageShell";
import { PanelGrid } from "@/components/panel/Panel";
import { Section } from "@/components/ui/section";
import { StatTile } from "@/components/ui/stat-tile";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useFormatters } from "@/lib/i18n/formatters";

import {
  ACTIVE_TASK_STATUSES,
  FAILING_TASK_STATUSES,
  type useFleetOverview,
} from "./use-fleet-overview";

export type FleetOverviewScreenProps = ReturnType<typeof useFleetOverview> & {
  /** The live fleet topology map (FleetMapPanel with its data). */
  map: React.ReactNode;
};

function statusTone(status: string): "default" | "secondary" | "destructive" | "outline" {
  if (status === "running") return "default";
  if (FAILING_TASK_STATUSES.has(status)) return "destructive";
  if (ACTIVE_TASK_STATUSES.has(status)) return "secondary";
  return "outline";
}

/**
 * /fleet: the live command center for agents, runtimes, dispatch and task
 * health. An overview (list rule 3): the counts, the topology map, then the
 * recent tasks, the runtimes and the roster as summaries of their top rows,
 * each with "View all" to its own list. Pure; the data half is
 * useFleetOverview.
 */
export function FleetOverviewScreen({
  agentCount,
  runningAgentCount,
  runtimeCount,
  activeTaskCount,
  incidentCount,
  loading,
  runtimes,
  runtimesLoading,
  runtimesError,
  refresh,
  recentTasks,
  tasksLoading,
  tasksError,
  agents,
  agentsLoading,
  agentsError,
  map,
}: FleetOverviewScreenProps) {
  const copy = useTranslations("fleetOverview");
  const taskStatus = useTranslations("agentActivity.statuses");
  const fmt = useFormatters();
  const age = (iso: string | null) => (iso ? fmt.formatRelativeTime(iso) : "—");
  return (
    <PageShell
      title={copy("title")}
      description={copy("description")}
      actions={
        <>
          <Button variant="outline" size="sm" asChild>
            <Link href="/fleet/map">{copy("openMap")}</Link>
          </Button>
          <Button variant="outline" size="sm" onClick={refresh}>
            <RefreshCwIcon className="mr-2 size-4" /> {copy("refresh")}
          </Button>
        </>
      }
    >
      <Section title={copy("atAGlance")} description={copy("atAGlanceDescription")}>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatTile
            label={copy("agents")}
            value={agentCount}
            icon={BotIcon}
            loading={loading}
            href="/agents?tab=registry"
            footer={copy("activeNow", { count: runningAgentCount })}
          />
          <StatTile
            label={copy("runtimes")}
            value={runtimeCount}
            icon={CpuIcon}
            loading={loading}
            href="/agents?tab=boxes"
            footer={copy("runtimesFooter")}
          />
          <StatTile
            label={copy("activeTasks")}
            value={activeTaskCount}
            icon={ListChecksIcon}
            loading={loading}
            href="/agents?tab=active"
            footer={copy("activeTasksFooter")}
          />
          <StatTile
            label={copy("incidents")}
            value={incidentCount}
            icon={AlertTriangleIcon}
            loading={loading}
            href="/agents?tab=history"
            footer={copy("incidentsFooter")}
          />
        </div>
      </Section>

      <Section title={copy("topology")} description={copy("topologyDescription")}>
        <Card className="overflow-hidden">{map}</Card>
      </Section>

      <PanelGrid>
        <ListSummary<AstroliftAgentTask>
          span={4}
          title={copy("recentActivity")}
          icon={<Clock3Icon className="size-4" />}
          description={copy("recentActivityDescription")}
          count={recentTasks.length}
          rows={recentTasks}
          keyOf={(t) => t.id}
          rowHref={(t) => `/agents/runs/${encodeURIComponent(t.id)}`}
          viewAllHref="/agents?tab=history"
          loading={tasksLoading}
          error={tasksError}
          onRetry={refresh}
          empty={{
            icon: <Clock3Icon className="size-5" />,
            title: copy("noActivity"),
            description: copy("noActivityDescription"),
            actionHref: "/agents?tab=dispatch",
            actionLabel: copy("openDispatch"),
          }}
          renderRow={(t) => (
            <span className="flex min-w-0 items-center gap-2">
              <span className="min-w-0 flex-1 truncate font-medium">
                {t.agentName || t.agentSlug || copy("agentTask")}
              </span>
              <Badge variant={statusTone(t.status)} className="shrink-0 capitalize">
                {taskStatus.has(t.status) ? taskStatus(t.status) : t.status.replace(/_/g, " ")}
              </Badge>
              <span className="text-muted-foreground shrink-0 font-mono text-xs">
                {age(t.createdAt)}
              </span>
            </span>
          )}
        />

        <ListSummary<(typeof runtimes)[number]>
          span={4}
          title={copy("runtimeHealth")}
          icon={<RadioTowerIcon className="size-4" />}
          description={copy("runtimeHealthDescription")}
          count={runtimes.length}
          rows={runtimes}
          keyOf={(r) => r.id}
          viewAllHref="/agents?tab=boxes"
          loading={runtimesLoading && runtimes.length === 0}
          error={runtimesError}
          onRetry={refresh}
          empty={{
            icon: <CpuIcon className="size-5" />,
            title: copy("noRuntimes"),
            description: copy("noRuntimesDescription"),
            actionHref: "/agents?tab=boxes",
            actionLabel: copy("manageRuntimes"),
          }}
          renderRow={(r) => (
            <span className="flex min-w-0 items-center justify-between gap-3">
              <span className="min-w-0">
                <span className="block truncate font-medium">{r.name}</span>
                <span className="text-muted-foreground block truncate font-mono text-xs">
                  {r.runtime} · {r.agentType}
                </span>
              </span>
              <CircleDotIcon
                className="text-success size-4 shrink-0"
                aria-label={copy("available")}
              />
            </span>
          )}
        />

        <ListSummary<AstroliftAgentListItem>
          span={4}
          title={copy("roster")}
          icon={<BotIcon className="size-4" />}
          description={copy("rosterDescription")}
          count={agents.length}
          rows={agents}
          keyOf={(a) => a.id}
          rowHref={(a) => `/agents/${encodeURIComponent(a.slug)}`}
          viewAllHref="/agents"
          loading={agentsLoading}
          error={agentsError}
          onRetry={refresh}
          empty={{
            icon: <BotIcon className="size-5" />,
            title: copy("noAgents"),
            description: copy("noAgentsDescription"),
            actionHref: "/agents?tab=registry",
            actionLabel: copy("openRegistry"),
          }}
          renderRow={(a) => (
            <span className="flex min-w-0 items-center gap-2">
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{a.name}</span>
                <span className="text-muted-foreground block truncate font-mono text-xs">
                  {a.projectSlug || "—"}
                </span>
              </span>
              <Badge variant={a.runningCount > 0 ? "default" : "outline"} className="shrink-0">
                {a.runningCount > 0 ? (
                  <span className="font-mono">{copy("running", { count: a.runningCount })}</span>
                ) : (
                  copy("idle")
                )}
              </Badge>
            </span>
          )}
        />
      </PanelGrid>
    </PageShell>
  );
}
