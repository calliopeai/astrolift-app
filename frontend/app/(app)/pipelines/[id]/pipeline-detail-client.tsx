"use client";

import { useQuery } from "@apollo/client/react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
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

type DetailTab = "runs" | "logs" | "artifacts" | "triggers" | "runners";
const TABS: readonly DetailTab[] = ["runs", "logs", "artifacts", "triggers", "runners"];
const TAB_LABELS: Record<DetailTab, string> = {
  runs: "Runs",
  logs: "Logs",
  artifacts: "Artifacts",
  triggers: "Triggers",
  runners: "Runners",
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

      {/* Runs tab — run list + DAG visualization (#107) */}
      {tab === "runs" && (
        <div className="space-y-2">
          {runsLoading && runs.length === 0 && <Skeleton className="h-40 w-full" />}
          {!runsLoading && runs.length === 0 && (
            <EmptyState icon={<ActivityIcon className="size-5" />} title="No runs yet" description="Trigger a run via webhook or manual dispatch." />
          )}
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
      )}

      {/* Logs tab (#108) */}
      {tab === "logs" && (
        <EmptyState
          icon={<ScrollIcon className="size-5" />}
          title="Live log streaming"
          description="Select a run from the Runs tab to view its step-by-step logs."
        />
      )}

      {/* Artifacts tab (#109) */}
      {tab === "artifacts" && (
        <EmptyState
          icon={<DownloadIcon className="size-5" />}
          title="Artifact browser"
          description="Artifacts from pipeline runs — build outputs, test reports, and deployment packages."
        />
      )}

      {/* Triggers tab (#110) */}
      {tab === "triggers" && (
        <EmptyState
          icon={<SettingsIcon className="size-5" />}
          title="Trigger configuration"
          description="Configure webhook triggers, scheduled runs, and manual dispatch inputs."
        />
      )}

      {/* Runners tab (#111) */}
      {tab === "runners" && (
        <EmptyState
          icon={<UsersIcon className="size-5" />}
          title="Runner management"
          description="Self-hosted runners registered for this pipeline. Add runners to run jobs on your own infrastructure."
        />
      )}
    </div>
  );
}
