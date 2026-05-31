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

import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { gql } from "@apollo/client";
import { ListControls, SortableHeader } from "@/components/ListControls";
import { useListControls } from "@/hooks/use-list-controls";

// ---------------------------------------------------------------------------
// GraphQL
// ---------------------------------------------------------------------------

const LIST_PIPELINES = gql`
  query ListPipelines($limit: Int) {
    astroliftPipelines(limit: $limit) {
      id
      name
      repoUrl
      defaultBranch
      tomlPath
      createdAt
    }
  }
`;

const LIST_PIPELINE_RUNS = gql`
  query ListPipelineRuns($pipelineId: ID, $limit: Int) {
    astroliftPipelineRuns(pipelineId: $pipelineId, limit: $limit) {
      id
      runNumber
      triggerKind
      triggerRef
      triggerActor
      status
      startedAt
      finishedAt
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
  const { data, loading } = useQuery<{ astroliftPipelines: Pipeline[] }>(LIST_PIPELINES, {
    variables: { limit: 100 },
    fetchPolicy: "cache-and-network",
  });

  const pipelines = data?.astroliftPipelines ?? [];

  const ctrl = useListControls({
    data: pipelines,
    searchFn: (p) => [p.name, p.repoUrl, p.defaultBranch].join(" "),
    initialPageSize: 25,
    sortFn: (a, b, sort) => {
      let av = "";
      let bv = "";
      if (sort.key === "name") { av = a.name; bv = b.name; }
      else if (sort.key === "repo") { av = a.repoUrl; bv = b.repoUrl; }
      else if (sort.key === "branch") { av = a.defaultBranch; bv = b.defaultBranch; }
      const cmp = av.localeCompare(bv);
      return sort.dir === "asc" ? cmp : -cmp;
    },
  });

  if (loading && pipelines.length === 0) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </div>
    );
  }

  if (pipelines.length === 0) {
    return (
      <EmptyState
        icon={<GitBranchIcon className="size-5" />}
        title="No pipelines yet"
        description="Create a pipeline TOML in your repo and register it here."
        actionHref="/pipelines/new"
        actionLabel="New Pipeline"
      />
    );
  }

  return (
    <div className="space-y-3">
      <ListControls controls={ctrl} searchPlaceholder="Search pipelines…" />
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
                Repository
              </SortableHeader>
            </TableHead>
            <TableHead>
              <SortableHeader sortKey="branch" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                Default branch
              </SortableHeader>
            </TableHead>
            <TableHead>TOML path</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {ctrl.rows.map((p) => (
            <TableRow key={p.id}>
              <TableCell className="font-medium">
                <Link href={`/pipelines/${p.id}`} className="hover:underline">
                  {p.name}
                </Link>
              </TableCell>
              <TableCell className="text-muted-foreground text-sm">
                <a href={p.repoUrl} target="_blank" rel="noopener noreferrer" className="hover:underline">
                  {p.repoUrl.replace(/^https?:\/\//, "")}
                </a>
              </TableCell>
              <TableCell className="font-mono text-xs">{p.defaultBranch}</TableCell>
              <TableCell className="font-mono text-xs text-muted-foreground">{p.tomlPath}</TableCell>
              <TableCell className="text-right">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={triggering}
                  onClick={() => onTrigger(p)}
                >
                  <PlayIcon className="mr-1 size-3" />
                  Run
                </Button>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Run history tab (#107)
// ---------------------------------------------------------------------------

function RunHistoryTab() {
  const { data, loading } = useQuery<{ astroliftPipelineRuns: PipelineRun[] }>(
    LIST_PIPELINE_RUNS,
    { variables: { limit: 100 }, fetchPolicy: "cache-and-network" }
  );

  const runs = data?.astroliftPipelineRuns ?? [];

  if (loading && runs.length === 0) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    );
  }

  if (runs.length === 0) {
    return (
      <EmptyState
        icon={<ClockIcon className="size-5" />}
        title="No pipeline runs yet"
        description="Trigger a run manually or connect a webhook to your repository."
      />
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Run</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Trigger</TableHead>
          <TableHead>Ref</TableHead>
          <TableHead>Actor</TableHead>
          <TableHead>Duration</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {runs.map((run) => {
          const duration =
            run.startedAt && run.finishedAt
              ? Math.round(
                  (new Date(run.finishedAt).getTime() - new Date(run.startedAt).getTime()) / 1000
                ) + "s"
              : run.startedAt
              ? "running"
              : "—";

          return (
            <TableRow key={run.id}>
              <TableCell className="font-mono text-sm">#{run.runNumber}</TableCell>
              <TableCell>
                <StatusBadge status={run.status} />
              </TableCell>
              <TableCell className="text-muted-foreground text-sm capitalize">
                {run.triggerKind}
              </TableCell>
              <TableCell className="font-mono text-xs">
                {run.triggerRef?.replace(/^refs\/heads\//, "")}
              </TableCell>
              <TableCell className="text-muted-foreground text-sm">{run.triggerActor ?? "—"}</TableCell>
              <TableCell className="text-muted-foreground text-sm">{duration}</TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
