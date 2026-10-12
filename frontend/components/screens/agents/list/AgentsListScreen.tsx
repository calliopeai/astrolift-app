"use client";

import {
  BotIcon,
  ClipboardListIcon,
  GitBranchIcon,
  MoreHorizontalIcon,
  PlayIcon,
  PlusIcon,
  SettingsIcon,
  SlidersHorizontalIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import type { Column, EmptyStateSpec } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useFormatters } from "@/lib/i18n/formatters";

import { AgentGlyph } from "./AgentGlyph";
import {
  AGENT_STATUS_DOT,
  type AgentModelKey,
  type AgentRow,
  agentsCrumbs,
  RUN_STATUS_DOT,
  titleCase,
} from "./agents-list";

export interface AgentsListScreenProps {
  list: ListStateController;
  /** The page on screen, already filtered, searched, sorted and sliced. */
  rows: AgentRow[];
  /** Agents matching the view, filters and search, across all pages. */
  totalCount: number;
  loading: boolean;
  stale: boolean;
  error: { message: string } | null;
  onRetry: () => void;
  /** The Agents module's canCreate: New agent. */
  canCreate: boolean;
  /** `agent.dispatch`: Run now. */
  canRun: boolean;
  dispatching: boolean;
  onRun: (agent: AgentRow) => Promise<boolean>;
}

const agentHref = (a: Pick<AgentRow, "slug">, tail = "") =>
  `/agents/${encodeURIComponent(a.slug)}${tail}`;

// The row's link is an ::after overlay stretched across the whole row; a
// link in a cell has to sit above it to be reachable.
const ABOVE_ROW_LINK = "relative z-10";

const MODEL_KEY: Record<AgentModelKey, "managed" | "gateway" | "apiKey"> = {
  managed: "managed",
  gateway: "gateway",
  "api-key": "apiKey",
};

const RUN_FAMILIES = new Set(["task", "service"]);
const RUN_MODES = new Set(["once", "loop", "schedule", "trigger", "service"]);
const RUN_STATUSES = new Set([
  "running",
  "queued",
  "pending",
  "completed",
  "succeeded",
  "failed",
  "timed_out",
  "cancelled",
  "canceled",
]);

/** formatRunMode, read from the catalog: "Task · Schedule" or "Service". */
function useRunModeLabel() {
  const t = useTranslations("agentsList");
  return (runFamily: string, runMode: string): string => {
    const f = runFamily.toLowerCase();
    const m = runMode.toLowerCase();
    const family = RUN_FAMILIES.has(f) ? t(`family.${f}`) : titleCase(runFamily);
    const mode = RUN_MODES.has(m) ? t(`mode.${m}`) : titleCase(runMode);
    if (!mode || mode === family) return family || t("none");
    return `${family} · ${mode}`;
  };
}

function AgentStatus({ agent }: { agent: AgentRow }) {
  const t = useTranslations("agentsList");
  const fmt = useFormatters();
  const label =
    agent.status === "running" && agent.running > 0
      ? t("runningCount", { count: agent.running })
      : agent.status === "scheduled" && agent.nextScheduledAt
        ? t("next", { age: fmt.formatRelativeTime(agent.nextScheduledAt) })
        : t(`status.${agent.status}`);
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5 text-sm">
      <StatusDot status={AGENT_STATUS_DOT[agent.status]} />
      <span className="truncate" title={agent.nextScheduledAt ?? undefined}>
        {label}
      </span>
    </span>
  );
}

function LastRun({ agent }: { agent: AgentRow }) {
  const t = useTranslations("agentsList");
  const statuses = useTranslations("agentOverview.statuses");
  const fmt = useFormatters();
  if (!agent.lastRunStatus && !agent.lastRunAt) {
    return <span className="text-muted-foreground text-xs">{t("neverRun")}</span>;
  }
  const runStatus = (agent.lastRunStatus ?? "").toLowerCase();
  const dot = RUN_STATUS_DOT[(agent.lastRunStatus ?? "").toLowerCase()] ?? "muted";
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5 text-xs">
      {agent.lastRunStatus && (
        <>
          <StatusDot status={dot} />
          <span className="truncate">
            {RUN_STATUSES.has(runStatus) ? statuses(runStatus) : titleCase(agent.lastRunStatus)}
          </span>
        </>
      )}
      {agent.lastRunAt && (
        <span className="text-muted-foreground shrink-0 font-mono" title={agent.lastRunAt}>
          {fmt.formatRelativeTime(agent.lastRunAt)}
        </span>
      )}
    </span>
  );
}

function Model({ agent }: { agent: AgentRow }) {
  const t = useTranslations("agentsList");
  return agent.model ? (
    <span className="text-sm">{t(`model.${MODEL_KEY[agent.model]}`)}</span>
  ) : (
    <span className="text-muted-foreground text-xs">{t("agentDefault")}</span>
  );
}

function Clusters({ clusters }: { clusters: string[] }) {
  const t = useTranslations("agentsList");
  if (clusters.length === 0)
    return <span className="text-muted-foreground text-xs">{t("none")}</span>;
  return (
    <span className="flex min-w-0 items-center gap-1 font-mono text-xs">
      <span className="min-w-0 truncate" title={clusters.join(", ")}>
        {clusters[0]}
      </span>
      {clusters.length > 1 && (
        <span className="text-muted-foreground shrink-0">+{clusters.length - 1}</span>
      )}
    </span>
  );
}

function Coordinates({ agent }: { agent: AgentRow }) {
  const path = `${agent.projectSlug}/${agent.appSlug}/${agent.slug}`;
  return (
    <span className="text-muted-foreground block truncate font-mono text-xs" title={path}>
      {path}
    </span>
  );
}

/** A card: the fleet glyph, name, status, model and last run. */
function AgentCard({ agent }: { agent: AgentRow }) {
  const t = useTranslations("agentsList");
  const runModeLabel = useRunModeLabel();
  return (
    <div className="bg-card hover:bg-accent/30 flex h-full min-w-0 flex-col gap-3 rounded-md border p-4 transition-colors">
      <div className="flex min-w-0 items-start gap-3">
        <AgentGlyph status={agent.status} running={agent.running} />
        <div className="min-w-0 flex-1">
          <div className="truncate font-semibold" title={agent.name}>
            {agent.name}
          </div>
          <Coordinates agent={agent} />
        </div>
      </div>
      <AgentStatus agent={agent} />
      <div className="flex min-w-0 flex-wrap items-center gap-2 text-xs">
        <Badge variant="outline">{runModeLabel(agent.runFamily, agent.runMode)}</Badge>
        {agent.model && <Badge variant="secondary">{t(`model.${MODEL_KEY[agent.model]}`)}</Badge>}
        {agent.runtime && (
          <Badge variant="outline" className="max-w-full min-w-0 font-mono">
            <span className="min-w-0 truncate">{agent.runtime}</span>
          </Badge>
        )}
      </div>
      <div className="mt-auto min-w-0">
        <LastRun agent={agent} />
      </div>
    </div>
  );
}

/** The header's `⋯`: the Agents area's other ways in that used to be tabs here. */
function HeaderMenu() {
  const t = useTranslations("agentsList");
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="icon" className="size-8" aria-label={t("moreActions")}>
          <MoreHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-48">
        <DropdownMenuItem asChild>
          <Link href="/agents/runs/new">
            <PlayIcon className="size-4" />
            {t("runAnAgent")}
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link href="/tasks">
            <ClipboardListIcon className="size-4" />
            {t("agentRuns")}
          </Link>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/**
 * Agents › Agents (spec 44 §5.1, §4.4): the registered agents on the shared
 * list, views All · Mine · Paused, project, status, model, runtime and
 * cluster filters, numbered pages, list or cards. The page is only this list
 * (Leo's rule 3): dispatch is Run now on an agent (or Agents › Runs › Run
 * agent with inputs), runs are Agents › Runs, boxes are Workloads. Pure view;
 * the data half is useAgentsList.
 */
export function AgentsListScreen({
  list,
  rows,
  totalCount,
  loading,
  stale,
  error,
  onRetry,
  canCreate,
  canRun,
  dispatching,
  onRun,
}: AgentsListScreenProps) {
  const t = useTranslations("agentsList");
  const runModeLabel = useRunModeLabel();
  const empty: EmptyStateSpec = {
    icon: <BotIcon className="size-5" />,
    title: t("emptyTitle"),
    description: t("emptyDescription"),
    ...(canCreate ? { actionHref: "/agents/new", actionLabel: t("newAgent") } : {}),
    learnMoreHref: "https://github.com/calliopeai/astrolift-docs/blob/main/reference/agents.md",
  };

  const columns: Column<AgentRow>[] = [
    {
      id: "agent",
      header: t("columns.agent"),
      sortKey: "name",
      cellClassName: "max-w-80",
      cell: (a) => (
        <span className="flex min-w-0 items-center gap-2">
          <AgentGlyph status={a.status} running={a.running} className="size-7" />
          <span className="block min-w-0">
            <span className="block truncate font-medium" title={a.name}>
              {a.name}
            </span>
            <Coordinates agent={a} />
          </span>
        </span>
      ),
    },
    {
      id: "status",
      header: t("columns.status"),
      sortKey: "status",
      cell: (a) => <AgentStatus agent={a} />,
    },
    { id: "model", header: t("columns.model"), cell: (a) => <Model agent={a} /> },
    {
      id: "runtime",
      header: t("columns.runtime"),
      cell: (a) => (
        <span className="block min-w-0">
          <span className="block truncate font-mono text-xs">{a.runtime ?? t("default")}</span>
          <span className="text-muted-foreground block truncate text-xs">
            {runModeLabel(a.runFamily, a.runMode)}
          </span>
        </span>
      ),
    },
    {
      id: "cluster",
      header: t("columns.cluster"),
      cellClassName: "max-w-48",
      cell: (a) => <Clusters clusters={a.clusters} />,
    },
    {
      id: "repo",
      header: t("columns.repo"),
      cellClassName: "max-w-56",
      cell: (a) =>
        a.sourceRepo ? (
          a.sourceUrl ? (
            <a
              href={a.sourceUrl}
              target="_blank"
              rel="noreferrer"
              className={`${ABOVE_ROW_LINK} text-muted-foreground hover:text-foreground flex min-w-0 items-center gap-1 text-xs`}
            >
              <GitBranchIcon className="size-3.5 shrink-0" />
              <span className="min-w-0 truncate font-mono">{a.sourceRepo}</span>
            </a>
          ) : (
            <span className="text-muted-foreground flex min-w-0 items-center gap-1 text-xs">
              <GitBranchIcon className="size-3.5 shrink-0" />
              <span className="min-w-0 truncate font-mono">{a.sourceRepo}</span>
            </span>
          )
        ) : (
          <span className="text-muted-foreground text-xs">{t("none")}</span>
        ),
    },
    {
      id: "lastRun",
      header: t("columns.lastRun"),
      sortKey: "lastRun",
      cell: (a) => <LastRun agent={a} />,
    },
  ];

  return (
    <ListPage<AgentRow>
      header={{
        crumbs: agentsCrumbs(),
        title: t("title"),
        primaryAction: canCreate ? (
          <Button size="sm" asChild>
            <Link href="/agents/new">
              <PlusIcon className="size-4" />
              {t("newAgent")}
            </Link>
          </Button>
        ) : undefined,
        menu: <HeaderMenu />,
      }}
      list={list}
      label={t("title")}
      columns={columns}
      rows={rows}
      getRowId={(a) => a.id}
      rowHref={(a) => agentHref(a)}
      renderCard={(a) => <AgentCard agent={a} />}
      rowActions={(a) => (
        <>
          {canRun && (
            <DropdownMenuItem disabled={dispatching} onSelect={() => void onRun(a)}>
              <PlayIcon className="size-4" />
              {t("runNow")}
            </DropdownMenuItem>
          )}
          {canRun && (
            <DropdownMenuItem asChild>
              <Link href={`/agents/runs/new?agent=${encodeURIComponent(a.slug)}`}>
                <SlidersHorizontalIcon className="size-4" />
                {t("runWithInputs")}
              </Link>
            </DropdownMenuItem>
          )}
          <DropdownMenuItem asChild>
            <Link href={agentHref(a, "/configuration?section=run-mode")}>
              <SettingsIcon className="size-4" />
              {t("runModeAndTriggers")}
            </Link>
          </DropdownMenuItem>
        </>
      )}
      loading={loading}
      stale={stale}
      error={error}
      onRetry={onRetry}
      empty={empty}
      totalCount={totalCount}
    />
  );
}
