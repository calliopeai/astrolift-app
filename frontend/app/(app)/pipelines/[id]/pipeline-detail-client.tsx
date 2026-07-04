"use client";

import { useQuery } from "@apollo/client/react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { PipelineSecretsTab } from "./secrets-tab";
import {
  ActivityIcon,
  DownloadIcon,
  Loader2Icon,
  ScrollIcon,
  SettingsIcon,
  UsersIcon,
} from "lucide-react";
import { gql } from "@apollo/client";

import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PipelineDag, type PipelineDagStage } from "@/components/viz";

const GET_PIPELINE = gql`
  query GetPipeline($id: ID!) {
    astroliftPipeline(id: $id) {
      id
      name
      repoUrl
      defaultBranch
      tomlPath
      createdAt
    }
  }
`;

const GET_PIPELINE_RUNS = gql`
  query GetPipelineRuns($pipelineId: ID, $limit: Int) {
    astroliftPipelineRuns(pipelineId: $pipelineId, limit: $limit) {
      id
      runNumber
      status
      triggerKind
      triggerRef
      startedAt
      finishedAt
    }
  }
`;

// A single run's job graph — jobs are the DAG nodes, `job.needs` the edges.
const GET_PIPELINE_RUN_GRAPH = gql`
  query GetPipelineRunGraph($id: String!) {
    astroliftPipelineRun(id: $id) {
      id
      runNumber
      status
      jobRuns {
        id
        status
        startedAt
        finishedAt
        job {
          jobId
          name
          needs
        }
      }
    }
  }
`;

interface JobRunNode {
  id: string;
  status: string;
  startedAt: string | null;
  finishedAt: string | null;
  job: { jobId: string; name: string; needs: unknown };
}

interface RunGraphResp {
  astroliftPipelineRun: {
    id: string;
    runNumber: number;
    status: string;
    jobRuns: JobRunNode[];
  } | null;
}

type DetailTab = "runs" | "logs" | "artifacts" | "triggers" | "runners" | "secrets";
const TABS: readonly DetailTab[] = ["runs", "logs", "artifacts", "triggers", "runners", "secrets"];
const TAB_LABELS: Record<DetailTab, string> = {
  runs: "Runs",
  logs: "Logs",
  artifacts: "Artifacts",
  triggers: "Triggers",
  runners: "Runners",
  secrets: "Secrets",
};

interface PipelineRun {
  id: string;
  runNumber: number;
  status: string;
  triggerKind: string;
  triggerRef: string;
  startedAt: string | null;
  finishedAt: string | null;
}

export function PipelineDetailClient({ pipelineId }: { pipelineId: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab") as DetailTab | null;
  const tab: DetailTab = rawTab && TABS.includes(rawTab) ? rawTab : "runs";

  function setTab(next: DetailTab) {
    const params = new URLSearchParams(searchParams.toString());
    if (next === "runs") params.delete("tab");
    else params.set("tab", next);
    router.replace(`${pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  const { data: runsData, loading: runsLoading } = useQuery<{ astroliftPipelineRuns: PipelineRun[] }>(
    GET_PIPELINE_RUNS,
    {
      variables: { pipelineId, limit: 50 },
      skip: tab !== "runs",
      fetchPolicy: "cache-and-network",
      pollInterval: tab === "runs" ? 10000 : 0,
    }
  );

  const runs = runsData?.astroliftPipelineRuns ?? [];

  return (
    <div className="space-y-4">
      {/* Tab bar */}
      <div className="flex gap-1 border-b pb-0">
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

      {/* Runs tab — latest-run DAG + run list (#107) */}
      {tab === "runs" && (
        <div className="space-y-4">
          {runsLoading && runs.length === 0 && <Skeleton className="h-40 w-full" />}
          {!runsLoading && runs.length === 0 && (
            <EmptyState icon={<ActivityIcon className="size-5" />} title="No runs yet" description="Trigger a run via webhook or manual dispatch." />
          )}
          {runs.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-sm font-medium">
                <ActivityIcon className="size-4" />
                Latest run · #{runs[0].runNumber}
              </div>
              {/* key on the run id so the graph re-flows when the newest run changes */}
              <RunGraph key={runs[0].id} runId={runs[0].id} />
            </div>
          )}
          <div className="space-y-2">
          {runs.map((run) => (
            <div key={run.id} className="flex items-center gap-3 rounded-md border px-4 py-3 text-sm">
              <Badge variant={run.status === "success" ? "default" : run.status === "failure" ? "destructive" : "secondary"} className="shrink-0">
                {run.status}
              </Badge>
              <span className="font-mono text-xs font-medium">#{run.runNumber}</span>
              <span className="text-muted-foreground text-xs capitalize">{run.triggerKind}</span>
              <span className="font-mono text-xs text-muted-foreground">{run.triggerRef?.replace(/^refs\/heads\//, "")}</span>
              <span className="ml-auto text-muted-foreground text-xs">
                {run.startedAt ? new Date(run.startedAt).toLocaleString() : "—"}
              </span>
            </div>
          ))}
          </div>
        </div>
      )}

      {/* Logs tab (#108) — placeholder; per-run log streaming isn't built yet.
          Copy must not instruct the impossible "select a run" action (#910). */}
      {tab === "logs" && (
        <EmptyState
          icon={<ScrollIcon className="size-5" />}
          title="Log streaming — coming soon"
          description="Step-by-step log streaming for pipeline runs isn't available yet. The latest run's stage graph is on the Runs tab."
        />
      )}

      {/* Artifacts tab (#109) — inert placeholder (#912). */}
      {tab === "artifacts" && (
        <EmptyState
          icon={<DownloadIcon className="size-5" />}
          title="Artifact browser — coming soon"
          description="Browsing run artifacts (build outputs, test reports, deployment packages) isn't available yet."
        />
      )}

      {/* Triggers tab (#110) — inert placeholder (#912). */}
      {tab === "triggers" && (
        <EmptyState
          icon={<SettingsIcon className="size-5" />}
          title="Trigger configuration — coming soon"
          description="Configuring webhook, scheduled, and manual-dispatch triggers from here isn't available yet."
        />
      )}

      {/* Runners tab (#111) — inert placeholder (#912). */}
      {tab === "runners" && (
        <EmptyState
          icon={<UsersIcon className="size-5" />}
          title="Runner management — coming soon"
          description="Registering and managing self-hosted runners for this pipeline isn't available yet."
        />
      )}

      {/* Secrets tab (#100) */}
      {tab === "secrets" && (
        <PipelineSecretsTab pipelineId={pipelineId} />
      )}
    </div>
  );
}

/**
 * Renders a single run's jobs as a status-coloured DAG. Jobs are nodes;
 * `job.needs` (a JSON string array) supplies the edges. Polls while the run
 * is in flight so the graph tracks live status.
 */
function RunGraph({ runId }: { runId: string }) {
  const { data, loading } = useQuery<RunGraphResp>(GET_PIPELINE_RUN_GRAPH, {
    variables: { id: runId },
    fetchPolicy: "cache-and-network",
    pollInterval: 10000,
  });

  const run = data?.astroliftPipelineRun ?? null;

  const stages: PipelineDagStage[] = (run?.jobRuns ?? []).map((jr) => ({
    id: jr.job.jobId,
    name: jr.job.name,
    status: jr.status,
    // `needs` is a JSON scalar — coerce defensively to a string array.
    needs: Array.isArray(jr.job.needs) ? jr.job.needs.map(String) : [],
    startedAt: jr.startedAt,
    finishedAt: jr.finishedAt,
  }));

  if (loading && !run) return <Skeleton className="h-64 w-full" />;
  if (stages.length === 0) {
    return (
      <div className="text-muted-foreground rounded-md border border-dashed px-4 py-6 text-center text-sm">
        This run has no jobs to graph.
      </div>
    );
  }

  return <PipelineDag stages={stages} height={320} />;
}
