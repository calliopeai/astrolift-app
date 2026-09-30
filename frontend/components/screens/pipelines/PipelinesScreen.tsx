"use client";

import {
  CheckCircle2Icon,
  ClockIcon,
  GitBranchIcon,
  Loader2Icon,
  PlayIcon,
  PlusIcon,
  XCircleIcon,
} from "lucide-react";
import Link from "next/link";
import type * as React from "react";

import type { Column } from "@/components/data-table";
import { Can } from "@/components/Can";
import { ListPage } from "@/components/list/ListPage";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

import {
  PIPELINE_TABS,
  type Pipeline,
  type PipelineRunRow,
  type PipelineTab,
  type usePipelineList,
  type useRunHistory,
} from "./use-pipelines";

const TAB_LABELS: Record<PipelineTab, string> = { pipelines: "Pipelines", runs: "Run History" };

/**
 * Cells that carry their own links or buttons have to sit above
 * `rowHref`'s stretched row link, which is an overlay across the row.
 */
const ABOVE_ROW_LINK = "relative z-10";

function StatusBadge({ status }: { status: string }) {
  const map: Record<
    string,
    { variant: "default" | "secondary" | "destructive"; icon: React.ReactNode }
  > = {
    success: { variant: "default", icon: <CheckCircle2Icon className="size-3" /> },
    failure: { variant: "destructive", icon: <XCircleIcon className="size-3" /> },
    running: { variant: "secondary", icon: <Loader2Icon className="size-3 animate-spin" /> },
    pending: { variant: "secondary", icon: <ClockIcon className="size-3" /> },
    cancelled: { variant: "secondary", icon: <XCircleIcon className="size-3" /> },
  };
  const { variant, icon } = map[status] ?? { variant: "secondary" as const, icon: null };
  return (
    <Badge variant={variant} className="flex items-center gap-1">
      {icon}
      {status}
    </Badge>
  );
}

export interface PipelinesScreenProps {
  tab: PipelineTab;
  onTabChange: (next: PipelineTab) => void;
  /** The pipeline list tab (a PipelineListView behind its hook). */
  pipelinesTab: React.ReactNode;
  /** The run history tab (a RunHistoryView behind its hook). */
  runsTab: React.ReactNode;
}

/** The /pipelines screen (#106, #107): tab bar, New Pipeline, and the active tab. */
export function PipelinesScreen({ tab, onTabChange, pipelinesTab, runsTab }: PipelinesScreenProps) {
  return (
    <div className="space-y-4">
      {/* Tab bar */}
      <div className="flex items-center justify-between border-b pb-0">
        <div className="flex gap-1">
          {PIPELINE_TABS.map((t) => (
            <button
              key={t}
              onClick={() => onTabChange(t)}
              className={[
                "-mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors",
                t === tab
                  ? "border-primary text-foreground"
                  : "text-muted-foreground hover:text-foreground border-transparent",
              ].join(" ")}
            >
              {TAB_LABELS[t]}
            </button>
          ))}
        </div>
        {tab === "pipelines" && (
          <Can permission="app.update" loading={null}>
            <Button asChild size="sm">
              <Link href="/pipelines/new">
                <PlusIcon className="mr-1 size-4" /> New Pipeline
              </Link>
            </Button>
          </Can>
        )}
      </div>

      {tab === "pipelines" && pipelinesTab}
      {tab === "runs" && runsTab}
    </div>
  );
}

export type PipelineListViewProps = ReturnType<typeof usePipelineList> & {
  onTrigger: (p: Pipeline) => Promise<void>;
  triggering: boolean;
};

/**
 * The pipeline list tab (#106): the one embedded list on it (views All ·
 * Mine, cursor pages), Run in each row's `⋯`. Pure.
 */
export function PipelineListView({
  list,
  rows,
  totalCount,
  nextCursor,
  loading,
  error,
  onRetry,
  onTrigger,
  triggering,
}: PipelineListViewProps) {
  const columns: Column<Pipeline>[] = [
    {
      id: "name",
      header: "Name",
      cellClassName: "max-w-64",
      cell: (p) => (
        <span className="block truncate font-medium" title={p.name}>
          {p.name}
        </span>
      ),
    },
    {
      id: "repo",
      header: "Repository",
      cellClassName: cn("text-muted-foreground max-w-72 text-sm", ABOVE_ROW_LINK),
      cell: (p) => (
        <a
          href={p.repoUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="block truncate hover:underline"
          title={p.repoUrl}
        >
          {p.repoUrl.replace(/^https?:\/\//, "")}
        </a>
      ),
    },
    {
      id: "branch",
      header: "Default branch",
      cellClassName: "font-mono text-xs",
      cell: (p) => p.defaultBranch,
    },
    {
      id: "toml",
      header: "TOML path",
      cellClassName: "text-muted-foreground max-w-56",
      cell: (p) => (
        <span className="block truncate font-mono text-xs" title={p.tomlPath}>
          {p.tomlPath}
        </span>
      ),
    },
  ];

  return (
    <ListPage<Pipeline>
      embedded
      list={list}
      label="Pipelines"
      columns={columns}
      rows={rows}
      getRowId={(p) => p.id}
      rowHref={(p) => `/pipelines/${p.id}`}
      rowActions={(p) => (
        <DropdownMenuItem disabled={triggering} onSelect={() => void onTrigger(p)}>
          <PlayIcon className="size-4" />
          Run
        </DropdownMenuItem>
      )}
      loading={loading}
      error={error}
      onRetry={onRetry}
      totalCount={totalCount}
      nextCursor={nextCursor}
      empty={{
        icon: <GitBranchIcon className="size-5" />,
        title: "No pipelines yet",
        description: "Create a pipeline TOML in your repo and register it here.",
        actionHref: "/pipelines/new",
        actionLabel: "New Pipeline",
      }}
    />
  );
}

export type RunHistoryViewProps = ReturnType<typeof useRunHistory>;

/**
 * The run history tab (#107): one pipeline's runs, picked from a select,
 * on the one embedded list (views All · Mine · Failed, cursor pages). Pure.
 */
export function RunHistoryView({
  list,
  rows,
  totalCount,
  nextCursor,
  loading,
  error,
  onRetry,
  options,
  selected,
  onSelect,
  loadingOptions,
}: RunHistoryViewProps) {
  const columns: Column<PipelineRunRow>[] = [
    {
      id: "run",
      header: "Run",
      cellClassName: "font-mono text-sm",
      cell: (run) => `#${run.runNumber}`,
    },
    {
      id: "status",
      header: "Status",
      cell: (run) => <StatusBadge status={run.status} />,
    },
    {
      id: "trigger",
      header: "Trigger",
      cellClassName: "text-muted-foreground text-sm capitalize",
      cell: (run) => run.triggerKind,
    },
    {
      id: "ref",
      header: "Ref",
      cellClassName: "max-w-64",
      cell: (run) => (
        <span className="block truncate font-mono text-xs" title={run.triggerRef}>
          {run.triggerRef?.replace(/^refs\/heads\//, "")}
        </span>
      ),
    },
    {
      id: "actor",
      header: "Actor",
      cellClassName: "text-muted-foreground max-w-56 text-sm",
      cell: (run) => (
        <span className="block truncate" title={run.triggerActor ?? undefined}>
          {run.triggerActor ?? "—"}
        </span>
      ),
    },
    {
      id: "duration",
      header: "Duration",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (run) =>
        run.startedAt && run.finishedAt
          ? `${Math.round(
              (new Date(run.finishedAt).getTime() - new Date(run.startedAt).getTime()) / 1000
            )}s`
          : run.startedAt
            ? "running"
            : "—",
    },
  ];

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <Select value={selected ?? ""} onValueChange={onSelect} disabled={options.length === 0}>
        <SelectTrigger size="sm" className="w-56 max-w-full" aria-label="Pipeline">
          <SelectValue placeholder={loadingOptions ? "Loading pipelines…" : "Select a pipeline"} />
        </SelectTrigger>
        <SelectContent>
          {options.map((p) => (
            <SelectItem key={p.id} value={p.id}>
              {p.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <ListPage<PipelineRunRow>
        embedded
        list={list}
        label="Pipeline runs"
        columns={columns}
        rows={rows}
        getRowId={(run) => run.id}
        loading={loading || loadingOptions}
        error={error}
        onRetry={onRetry}
        totalCount={totalCount}
        nextCursor={nextCursor}
        empty={
          options.length === 0 && !loadingOptions
            ? {
                icon: <GitBranchIcon className="size-5" />,
                title: "No pipelines yet",
                description: "Register a pipeline before there is a run history to read.",
                actionHref: "/pipelines/new",
                actionLabel: "New Pipeline",
              }
            : {
                icon: <ClockIcon className="size-5" />,
                title: "No pipeline runs yet",
                description: "Trigger a run manually or connect a webhook to your repository.",
              }
        }
      />
    </div>
  );
}
