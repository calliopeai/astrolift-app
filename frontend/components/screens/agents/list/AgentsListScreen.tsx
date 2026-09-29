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

import type { Column, EmptyStateSpec } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/use-list-state";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { formatRelativeAge } from "@/lib/format";

import { AgentGlyph } from "./AgentGlyph";
import {
  AGENT_MODEL_LABEL,
  AGENT_STATUS_DOT,
  AGENT_STATUS_LABEL,
  type AgentRow,
  agentsCrumbs,
  formatRunMode,
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

function AgentStatus({ agent }: { agent: AgentRow }) {
  const label =
    agent.status === "running" && agent.running > 0
      ? `${agent.running} running`
      : agent.status === "scheduled" && agent.nextScheduledAt
        ? `Next ${formatRelativeAge(agent.nextScheduledAt)}`
        : AGENT_STATUS_LABEL[agent.status];
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
  if (!agent.lastRunStatus && !agent.lastRunAt) {
    return <span className="text-muted-foreground text-xs">Never run</span>;
  }
  const dot = RUN_STATUS_DOT[(agent.lastRunStatus ?? "").toLowerCase()] ?? "muted";
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5 text-xs">
      {agent.lastRunStatus && (
        <>
          <StatusDot status={dot} />
          <span className="truncate">{titleCase(agent.lastRunStatus)}</span>
        </>
      )}
      {agent.lastRunAt && (
        <span className="text-muted-foreground shrink-0 font-mono" title={agent.lastRunAt}>
          {formatRelativeAge(agent.lastRunAt)}
        </span>
      )}
    </span>
  );
}

function Model({ agent }: { agent: AgentRow }) {
  return agent.model ? (
    <span className="text-sm">{AGENT_MODEL_LABEL[agent.model]}</span>
  ) : (
    <span className="text-muted-foreground text-xs">Agent default</span>
  );
}

function Clusters({ clusters }: { clusters: string[] }) {
  if (clusters.length === 0) return <span className="text-muted-foreground text-xs">none</span>;
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
        <Badge variant="outline">{formatRunMode(agent.runFamily, agent.runMode)}</Badge>
        {agent.model && <Badge variant="secondary">{AGENT_MODEL_LABEL[agent.model]}</Badge>}
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
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="icon" className="size-8" aria-label="More agent actions">
          <MoreHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-48">
        <DropdownMenuItem asChild>
          <Link href="/agents/runs/new">
            <PlayIcon className="size-4" />
            Run an agent
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link href="/tasks">
            <ClipboardListIcon className="size-4" />
            Agent runs
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
  const empty: EmptyStateSpec = {
    icon: <BotIcon className="size-5" />,
    title: "No agents registered",
    description:
      "Register an agent repo to scan it for agent manifests and add each one as an agent here. Agents share the same image build and deployment pipeline as your apps.",
    ...(canCreate ? { actionHref: "/agents/new", actionLabel: "New agent" } : {}),
    learnMoreHref: "https://github.com/calliopeai/astrolift-docs/blob/main/reference/agents.md",
  };

  const columns: Column<AgentRow>[] = [
    {
      id: "agent",
      header: "Agent",
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
    { id: "status", header: "Status", sortKey: "status", cell: (a) => <AgentStatus agent={a} /> },
    { id: "model", header: "Model", cell: (a) => <Model agent={a} /> },
    {
      id: "runtime",
      header: "Runtime",
      cell: (a) => (
        <span className="block min-w-0">
          <span className="block truncate font-mono text-xs">{a.runtime ?? "default"}</span>
          <span className="text-muted-foreground block truncate text-xs">
            {formatRunMode(a.runFamily, a.runMode)}
          </span>
        </span>
      ),
    },
    {
      id: "cluster",
      header: "Cluster",
      cellClassName: "max-w-48",
      cell: (a) => <Clusters clusters={a.clusters} />,
    },
    {
      id: "repo",
      header: "Repo",
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
          <span className="text-muted-foreground text-xs">none</span>
        ),
    },
    { id: "lastRun", header: "Last run", sortKey: "lastRun", cell: (a) => <LastRun agent={a} /> },
  ];

  return (
    <ListPage<AgentRow>
      header={{
        crumbs: agentsCrumbs(),
        title: "Agents",
        primaryAction: canCreate ? (
          <Button size="sm" asChild>
            <Link href="/agents/new">
              <PlusIcon className="size-4" />
              New agent
            </Link>
          </Button>
        ) : undefined,
        menu: <HeaderMenu />,
      }}
      list={list}
      label="Agents"
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
              Run now
            </DropdownMenuItem>
          )}
          {canRun && (
            <DropdownMenuItem asChild>
              <Link href={`/agents/runs/new?agent=${encodeURIComponent(a.slug)}`}>
                <SlidersHorizontalIcon className="size-4" />
                Run with inputs
              </Link>
            </DropdownMenuItem>
          )}
          <DropdownMenuItem asChild>
            <Link href={agentHref(a, "/configuration?section=run-mode")}>
              <SettingsIcon className="size-4" />
              Run mode and triggers
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
