"use client";

import { useState } from "react";
import Link from "next/link";
import {
  ActivityIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  ClockIcon,
  Loader2Icon,
  PlusIcon,
  TrashIcon,
  WrenchIcon,
} from "lucide-react";
import { useQuery } from "@apollo/client/react";
import { toast } from "sonner";
import { useSearchParams, useRouter, usePathname } from "next/navigation";

import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { useConfirm } from "@/hooks/use-confirm";
import { useWorkflows, useDeleteWorkflow } from "@/graphql/workflows/workflows.hooks";
import { LIST_WORKFLOW_RUNS } from "@/graphql/operations/operations.queries";
import type { AstroliftWorkflowRun } from "@/graphql/operations/operations.types";
import type { WorkflowDefinition } from "@/graphql/workflows/workflows.types";

interface WorkflowRunsResp {
  astroliftWorkflowRuns: AstroliftWorkflowRun[];
}

import { WorkflowInstancesPanel } from "./instances-panel";

type WorkflowTab = "running" | "definitions" | "history";
const TABS: readonly WorkflowTab[] = ["running", "definitions", "history"];

const TAB_LABELS: Record<WorkflowTab, string> = {
  running: "Running",
  definitions: "Definitions",
  history: "History",
};

export default function WorkflowsPage() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab") as WorkflowTab | null;
  const tab: WorkflowTab = rawTab && TABS.includes(rawTab) ? rawTab : "running";

  function setTab(next: WorkflowTab) {
    const params = new URLSearchParams(searchParams.toString());
    if (next === "running") {
      params.delete("tab");
    } else {
      params.set("tab", next);
    }
    router.replace(`${pathname}${params.size ? `?${params}` : ""}`, { scroll: false });
  }

  const { workflows, loading: defsLoading, error: defsError } = useWorkflows();
  const [deleteWorkflow] = useDeleteWorkflow();
  const confirm = useConfirm();
  const [expandedSlug, setExpandedSlug] = useState<string | null>(null);

  const { data: runsData, loading: runsLoading } = useQuery<WorkflowRunsResp>(LIST_WORKFLOW_RUNS, {
    variables: { limit: 50 },
    skip: tab !== "history",
    fetchPolicy: "cache-and-network",
  });
  const historyRuns = runsData?.astroliftWorkflowRuns ?? [];

  const handleDelete = async (wf: WorkflowDefinition) => {
    const ok = await confirm({
      title: `Delete "${wf.name}"?`,
      description: "This will permanently delete the workflow definition and cannot be undone.",
      confirmLabel: "Delete",
      cancelLabel: "Cancel",
    });
    if (!ok) return;
    const { data } = await deleteWorkflow({ variables: { slug: wf.slug } });
    if (data?.deleteWorkflowDefinition?.ok) {
      toast.success("Workflow deleted", { description: `${wf.name} has been removed.` });
    } else {
      for (const e of data?.deleteWorkflowDefinition?.errors ?? []) {
        toast.error(`${e.field}: ${e.messages.join(", ")}`);
      }
    }
  };

  return (
    <PageShell
      title="Workflows"
      description="Automation workflow instances and definitions."
      actions={
        tab === "definitions" ? (
          <Button asChild>
            <Link href="/workflows/new">
              <PlusIcon className="mr-1 h-4 w-4" /> New Workflow
            </Link>
          </Button>
        ) : null
      }
    >
      {/* Tab bar */}
      <div className="flex gap-1 border-b pb-0 mb-4">
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

      {/* Running — fleet-wide Temporal instance monitoring */}
      {tab === "running" && (
        <div className="flex flex-col gap-4">
          <p className="text-muted-foreground text-sm">
            Active Temporal workflow instances across all apps and platform operations.
            Cancel or terminate stuck instances from here.
          </p>
          <WorkflowInstancesPanel workflowType="" />
        </div>
      )}

      {/* Definitions — WorkflowDefinition catalog */}
      {tab === "definitions" && (
        <div className="flex flex-col gap-4">
          {defsLoading && (
            <div className="flex items-center justify-center p-12">
              <Loader2Icon className="text-muted-foreground h-6 w-6 animate-spin" />
            </div>
          )}
          {defsError && (
            <div className="text-destructive bg-destructive/10 border-destructive/20 rounded-md border p-4 text-sm">
              Error: {defsError.message}
            </div>
          )}
          {!defsLoading && workflows.length === 0 && (
            <EmptyState
              icon={<WrenchIcon className="size-5" />}
              title="No workflow definitions"
              description="Workflow definitions let you model multi-step automation chains."
              actionHref="/workflows/new"
              actionLabel="New Workflow"
            />
          )}
          <div className="grid gap-4">
            {workflows.map((wf) => {
              const isExpanded = expandedSlug === wf.slug;
              return (
                <div key={wf.slug} className="flex flex-col gap-3 rounded-lg border p-4">
                  <div className="flex items-center justify-between">
                    <div className="flex flex-col gap-1">
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{wf.name}</span>
                        <Badge variant={wf.isEnabled ? "default" : "secondary"}>
                          {wf.isEnabled ? "Active" : "Disabled"}
                        </Badge>
                        <span className="text-muted-foreground text-xs">{wf.modelLabel}</span>
                      </div>
                      <span className="text-muted-foreground text-sm">
                        {wf.description || "No description"}
                      </span>
                    </div>
                    <div className="flex items-center gap-4">
                      <div className="text-muted-foreground text-sm">
                        {wf.activeInstanceCount} active / {wf.instanceCount} total
                      </div>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setExpandedSlug(isExpanded ? null : wf.slug)}
                      >
                        <ActivityIcon className="mr-1 h-3 w-3" />
                        Instances
                        {isExpanded ? (
                          <ChevronUpIcon className="ml-1 h-3 w-3" />
                        ) : (
                          <ChevronDownIcon className="ml-1 h-3 w-3" />
                        )}
                      </Button>
                      <Button variant="outline" size="sm" asChild>
                        <Link href={`/workflows/${wf.slug}/builder`}>
                          <WrenchIcon className="mr-1 h-3 w-3" /> Builder
                        </Link>
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleDelete(wf)}
                        className="text-destructive hover:text-destructive hover:bg-destructive/10"
                      >
                        <TrashIcon className="mr-1 h-3 w-3" /> Delete
                      </Button>
                    </div>
                  </div>
                  {isExpanded && (
                    <div className="border-t pt-3">
                      <WorkflowInstancesPanel workflowType={wf.name} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* History — completed / failed / terminated workflow runs */}
      {tab === "history" && (
        <div className="flex flex-col gap-4">
          {runsLoading && (
            <div className="flex items-center justify-center p-12">
              <Loader2Icon className="text-muted-foreground h-6 w-6 animate-spin" />
            </div>
          )}
          {!runsLoading && historyRuns.length === 0 && (
            <EmptyState
              icon={<ClockIcon className="size-5" />}
              title="No workflow run history"
              description="Completed, failed, and terminated workflow runs appear here."
            />
          )}
          {historyRuns.length > 0 && (
            <div className="flex flex-col gap-2">
              {historyRuns.map((run) => (
                <div
                  key={`${run.workflowId}-${run.runId}`}
                  className="flex items-center gap-3 rounded-md border px-4 py-3 text-sm"
                >
                  <Badge
                    variant={run.status === "completed" ? "default" : "destructive"}
                    className="shrink-0"
                  >
                    {run.status}
                  </Badge>
                  <span className="min-w-0 flex-1 truncate font-mono text-xs font-medium">
                    {run.workflowKind}
                  </span>
                  {run.registeredAppId && (
                    <Badge variant="outline" className="shrink-0 text-xs">
                      {run.registeredAppId}
                    </Badge>
                  )}
                  <span className="text-muted-foreground shrink-0 text-xs">
                    {run.startedAt ? new Date(run.startedAt).toLocaleString() : "—"}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </PageShell>
  );
}
