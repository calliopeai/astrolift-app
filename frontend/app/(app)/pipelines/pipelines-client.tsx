"use client";

import Link from "next/link";
import { useQuery, useMutation } from "@apollo/client/react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import {
  GitBranchIcon,
  PlayIcon,
  PlusIcon,
  Loader2Icon,
  CheckCircle2Icon,
  XCircleIcon,
  ClockIcon,
} from "lucide-react";
import { toast } from "sonner";

import {
  DataTable,
  useCursorTable,
  type Column,
  type CursorPage,
} from "@/components/data-table";
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
import { gql } from "@apollo/client";

// ---------------------------------------------------------------------------
// GraphQL
//
// Kept inline, as the rest of this file's operations are. Both fields are
// the cursor-paginated ones: `{ items, nextCursor, totalCount }` out,
// `limit` + `after` (+ `search`) in, which is what `useCursorTable` walks.
// `$limit: Int` is nullable against the schema's `limit: Int! = 50` because
// that argument carries a default; `$pipelineId: String!` does not, so the
// run stream declares it non-null.
// ---------------------------------------------------------------------------

const LIST_PIPELINES_PAGE = gql`
  query ListPipelinesPage($search: String, $limit: Int, $after: String) {
    astroliftPipelinesPage(search: $search, limit: $limit, after: $after) {
      items {
        id
        name
        repoUrl
        defaultBranch
        tomlPath
        createdAt
      }
      nextCursor
      totalCount
    }
  }
`;

const LIST_PIPELINE_RUNS_PAGE = gql`
  query ListPipelineRunsPage(
    $pipelineId: String!
    $search: String
    $limit: Int
    $after: String
  ) {
    astroliftPipelineRunsPage(
      pipelineId: $pipelineId
      search: $search
      limit: $limit
      after: $after
    ) {
      items {
        id
        runNumber
        triggerKind
        triggerRef
        triggerActor
        status
        startedAt
        finishedAt
      }
      nextCursor
      totalCount
    }
  }
`;

const TRIGGER_PIPELINE_RUN = gql`
  mutation TriggerPipelineRun($input: TriggerPipelineRunInput!) {
    triggerPipelineRun(input: $input) {
      ok
      errors { code message }
      data {
        id
        runNumber
        status
      }
    }
  }
`;

// ---------------------------------------------------------------------------
// Mutation response type

interface TriggerPipelineRunResp {
  triggerPipelineRun: {
    ok: boolean;
    errors: { code: string; message: string }[];
    data: { id: string; runNumber: number; status: string } | null;
  };
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type PipelineTab = "pipelines" | "runs";
const TABS: readonly PipelineTab[] = ["pipelines", "runs"];
const TAB_LABELS: Record<PipelineTab, string> = { pipelines: "Pipelines", runs: "Run History" };

interface Pipeline {
  id: string;
  name: string;
  repoUrl: string;
  defaultBranch: string;
  tomlPath: string;
  createdAt: string;
}

interface PipelineRun {
  id: string;
  runNumber: number;
  triggerKind: string;
  triggerRef: string;
  triggerActor: string | null;
  status: string;
  startedAt: string | null;
  finishedAt: string | null;
}

interface PipelinesPageResp {
  astroliftPipelinesPage: CursorPage<Pipeline>;
}

interface RunsPageResp {
  astroliftPipelineRunsPage: CursorPage<PipelineRun>;
}

/**
 * Cells that carry their own links or buttons have to sit above
 * `rowHref`'s stretched row link, which is an overlay across the row.
 */
const ABOVE_ROW_LINK = "relative z-10";

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { variant: "default" | "secondary" | "destructive"; icon: React.ReactNode }> = {
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

// ---------------------------------------------------------------------------
// Root client component (#106, #107)
// ---------------------------------------------------------------------------

export function PipelinesClient() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab") as PipelineTab | null;
  const tab: PipelineTab = rawTab && TABS.includes(rawTab) ? rawTab : "pipelines";

  function setTab(next: PipelineTab) {
    const params = new URLSearchParams(searchParams.toString());
    if (next === "pipelines") params.delete("tab");
    else params.set("tab", next);
    router.replace(`${pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  const [triggerPipelineRun, { loading: triggering }] = useMutation<TriggerPipelineRunResp>(TRIGGER_PIPELINE_RUN);

  async function handleManualTrigger(pipeline: Pipeline) {
    const { data } = await triggerPipelineRun({
      variables: { input: { pipelineId: pipeline.id } },
    });
    if (data?.triggerPipelineRun?.ok) {
      const run = data.triggerPipelineRun.data;
      toast.success(`Run #${run?.runNumber} started`, {
        description: `Pipeline: ${pipeline.name}`,
      });
    } else {
      for (const e of data?.triggerPipelineRun?.errors ?? []) {
        toast.error(`${e.code}: ${e.message}`);
      }
    }
  }

  return (
    <div className="space-y-4">
      {/* Tab bar */}
      <div className="flex items-center justify-between border-b pb-0">
        <div className="flex gap-1">
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={[
                "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
                t === tab
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
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

      {tab === "pipelines" && (
        <PipelineListTab onTrigger={handleManualTrigger} triggering={triggering} />
      )}
      {tab === "runs" && <RunHistoryTab />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pipeline list tab (#106)
// ---------------------------------------------------------------------------

function PipelineListTab({
  onTrigger,
  triggering,
}: {
  onTrigger: (p: Pipeline) => Promise<void>;
  triggering: boolean;
}) {
  // `astroliftPipelinesPage` takes `search`, `limit` and `after` only —
  // no sort argument, so no column declares a `sortKey`. The name / repo /
  // branch comparators this tab used to run reordered one page of a
  // server-ordered catalogue, which is the wrong order at every boundary.
  const table = useCursorTable<Pipeline>({
    query: LIST_PIPELINES_PAGE,
    extract: (d) => (d as PipelinesPageResp | undefined)?.astroliftPipelinesPage,
    searchVariable: "search",
    urlKey: "pipe",
  });

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

// ---------------------------------------------------------------------------
// Run history tab (#107)
// ---------------------------------------------------------------------------

/**
 * `astroliftPipelineRunsPage` is single-pipeline by construction —
 * `run_number` is a per-pipeline counter, which is what makes it a valid
 * seek key — so `pipelineId` is a required argument. The cross-pipeline
 * stream this tab used to ask for does not exist server-side: it sent a
 * nullable `$pipelineId: ID` into a `String!` argument, which the server
 * rejects at validation, so the tab has been rendering its empty state
 * unconditionally. It now picks a pipeline instead.
 */
function RunHistoryTab() {
  const [pipelineId, setPipelineId] = useState<string | null>(null);

  const pipelines = useQuery<PipelinesPageResp>(LIST_PIPELINES_PAGE, {
    variables: { limit: 100 },
    fetchPolicy: "cache-and-network",
  });
  const options = pipelines.data?.astroliftPipelinesPage.items ?? [];
  const selected = pipelineId ?? options[0]?.id ?? null;
  const loadingOptions = pipelines.loading && options.length === 0;

  const table = useCursorTable<PipelineRun>({
    query: LIST_PIPELINE_RUNS_PAGE,
    variables: { pipelineId: selected },
    extract: (d) => (d as RunsPageResp | undefined)?.astroliftPipelineRunsPage,
    searchVariable: "search",
    urlKey: "run",
    skip: !selected,
  });

  const columns: Column<PipelineRun>[] = [
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
        <Select
          value={selected ?? ""}
          onValueChange={setPipelineId}
          disabled={options.length === 0}
        >
          <SelectTrigger size="sm" className="w-56" aria-label="Pipeline">
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
                description:
                  "Trigger a run manually or connect a webhook to your repository.",
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
