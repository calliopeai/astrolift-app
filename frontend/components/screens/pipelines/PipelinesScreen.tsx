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

import { DataTable, type Column } from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
          <Button asChild size="sm">
            <Link href="/pipelines/new">
              <PlusIcon className="mr-1 size-4" /> New Pipeline
            </Link>
          </Button>
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

/** The pipeline list tab (#106). */
export function PipelineListView({ table, onTrigger, triggering }: PipelineListViewProps) {
  const columns: Column<Pipeline>[] = [
    {
      id: "name",
      header: "Name",
      cell: (p) => <span className="font-medium">{p.name}</span>,
    },
    {
      id: "repo",
      header: "Repository",
      cellClassName: cn("text-muted-foreground text-sm", ABOVE_ROW_LINK),
      cell: (p) => (
        <a href={p.repoUrl} target="_blank" rel="noopener noreferrer" className="hover:underline">
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
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (p) => p.tomlPath,
    },
    {
      id: "actions",
      header: "Actions",
      align: "right",
      width: "w-24",
      cellClassName: ABOVE_ROW_LINK,
      cell: (p) => (
        <Button variant="outline" size="sm" disabled={triggering} onClick={() => onTrigger(p)}>
          <PlayIcon className="mr-1 size-3" />
          Run
        </Button>
      ),
    },
  ];

  return (
    <DataTable
      label="Pipelines"
      controller={table}
      columns={columns}
      getRowId={(p) => p.id}
      rowHref={(p) => `/pipelines/${p.id}`}
      searchPlaceholder="Search pipelines…"
      empty={{
        icon: <GitBranchIcon className="size-5" />,
        title: "No pipelines yet",
        description: "Create a pipeline TOML in your repo and register it here.",
        actionHref: "/pipelines/new",
        actionLabel: "New Pipeline",
      }}
      emptyFiltered={{
        title: "No matching pipelines",
        description:
          "No pipeline matches that search. The server matches the pipeline name, its repository URL, and the app it deploys.",
      }}
    />
  );
}

export type RunHistoryViewProps = ReturnType<typeof useRunHistory>;

/** The run history tab (#107): one pipeline's runs, picked from a select. */
export function RunHistoryView({
  table,
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
      cellClassName: "font-mono text-xs",
      cell: (run) => run.triggerRef?.replace(/^refs\/heads\//, ""),
    },
    {
      id: "actor",
      header: "Actor",
      cellClassName: "text-muted-foreground text-sm",
      cell: (run) => run.triggerActor ?? "—",
    },
    {
      id: "duration",
      header: "Duration",
      cellClassName: "text-muted-foreground text-sm",
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
    <DataTable
      label="Pipeline runs"
      controller={table}
      columns={columns}
      getRowId={(run) => run.id}
      searchPlaceholder="Search runs…"
      toolbar={
        <Select value={selected ?? ""} onValueChange={onSelect} disabled={options.length === 0}>
          <SelectTrigger size="sm" className="w-56" aria-label="Pipeline">
            <SelectValue
              placeholder={loadingOptions ? "Loading pipelines…" : "Select a pipeline"}
            />
          </SelectTrigger>
          <SelectContent>
            {options.map((p) => (
              <SelectItem key={p.id} value={p.id}>
                {p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      }
      empty={
        loadingOptions
          ? { icon: <Loader2Icon className="size-5 animate-spin" />, title: "Loading pipelines…" }
          : options.length === 0
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
      emptyFiltered={{
        title: "No matching runs",
        description:
          "No run matches that search. The server matches the triggering ref, the actor, and the trigger kind.",
      }}
    />
  );
}
