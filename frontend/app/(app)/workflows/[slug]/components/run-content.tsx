"use client";

import { Loader2Icon, PlayIcon, PowerIcon, PowerOffIcon } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import {
  useRunWorkflow,
  useUpdateConfiguredWorkflow,
  useWorkflowsEntitlement,
} from "@/graphql/workflows/tiered.hooks";
import type { TieredWorkflowRun } from "@/graphql/workflows/tiered.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { formatTriggerKind, type WorkflowDetailRenderProps } from "./workflow-detail-shell";

/** Newest run by startedAt (the detail query doesn't guarantee order). */
export function latestRun(runs: TieredWorkflowRun[]): TieredWorkflowRun | null {
  if (runs.length === 0) return null;
  return runs.reduce((newest, run) => (run.startedAt > newest.startedAt ? run : newest));
}

/**
 * Is a run finished? `isCompleted` is authoritative; the currentState string
 * match is a backstop for terminal states (failed/cancelled/terminated) that
 * may report before the flag flips. Drives live-DAG poll gating (#1090) — a
 * null run (nothing selected) counts as terminal so nothing polls.
 */
export function isRunTerminal(run: TieredWorkflowRun | null): boolean {
  if (!run) return true;
  if (run.isCompleted) return true;
  const s = run.currentState.toLowerCase();
  return (
    s.includes("fail") ||
    s.includes("error") ||
    s.includes("terminat") ||
    s.includes("cancel") ||
    s.includes("complet") ||
    s.includes("succe") ||
    s.includes("done")
  );
}

/** currentState is a free String! — map it onto a badge variant loosely. */
export function RunStateBadge({ state }: { state: string }) {
  const s = state.toLowerCase();
  const variant: "default" | "secondary" | "destructive" | "outline" =
    s.includes("fail") || s.includes("error") || s.includes("terminat")
      ? "destructive"
      : s.includes("complete") || s.includes("succe") || s.includes("done")
        ? "secondary"
        : s.includes("cancel")
          ? "outline"
          : "default";
  return <Badge variant={variant}>{state}</Badge>;
}

/**
 * Run pillar — dispatch-now (gated `canRun`), the trigger configuration
 * summary with an enable/disable toggle (gated `canManage`), and the latest
 * run's status. All affordances render exactly what `me.modules` answered —
 * no client permission math.
 */
export function RunContent({ workflow, refetch }: WorkflowDetailRenderProps) {
  const fmt = useFormatters();
  const entitlement = useWorkflowsEntitlement();
  const [runWorkflow, { loading: running }] = useRunWorkflow();
  const [updateWorkflow, { loading: toggling }] = useUpdateConfiguredWorkflow();

  const latest = latestRun(workflow.runs);

  const handleRun = async () => {
    const { data } = await runWorkflow({
      variables: { workflowId: workflow.guid, orgId: workflow.organizationGuid },
    });
    if (data?.runWorkflow?.ok) {
      toast.success("Run started", {
        description: data.runWorkflow.workflowRunId
          ? `Run ${data.runWorkflow.workflowRunId}`
          : workflow.name,
      });
      refetch();
    } else {
      const errors = data?.runWorkflow?.errors ?? [];
      if (errors.length > 0) {
        for (const e of errors) toast.error(`${e.field}: ${e.messages.join(", ")}`);
      } else {
        toast.error("Failed to start run");
      }
    }
  };

  const handleToggle = async () => {
    const next = !workflow.isEnabled;
    const { data } = await updateWorkflow({
      variables: { slug: workflow.slug, isEnabled: next, orgId: workflow.organizationGuid },
    });
    if (data?.updateWorkflow?.ok) {
      toast.success(next ? "Workflow enabled" : "Workflow disabled");
      refetch();
    } else {
      const errors = data?.updateWorkflow?.errors ?? [];
      if (errors.length > 0) {
        for (const e of errors) toast.error(`${e.field}: ${e.messages.join(", ")}`);
      } else {
        toast.error("Failed to update workflow");
      }
    }
  };

  return (
    <div className="flex flex-col gap-8">
      <Section
        title="Run now"
        description="Dispatch a run of this workflow immediately."
        action={
          entitlement.canRun ? (
            <Button onClick={handleRun} disabled={running || !workflow.isEnabled}>
              {running ? (
                <Loader2Icon className="mr-1 size-4 animate-spin" />
              ) : (
                <PlayIcon className="mr-1 size-4" />
              )}
              Run now
            </Button>
          ) : null
        }
      >
        {!workflow.isEnabled && entitlement.canRun && (
          <p className="text-muted-foreground text-sm">Enable this workflow to run it.</p>
        )}
        {latest ? (
          <div className="flex flex-wrap items-center gap-3 rounded-md border px-4 py-3 text-sm">
            <span className="text-muted-foreground">Latest run</span>
            <RunStateBadge state={latest.currentState} />
            <span className="text-muted-foreground text-xs">
              started {fmt.formatDateTime(latest.startedAt)}
            </span>
            {latest.completedAt && (
              <span className="text-muted-foreground text-xs">
                completed {fmt.formatDateTime(latest.completedAt)}
              </span>
            )}
            {latest.temporalWorkflowId && (
              <span className="text-muted-foreground font-mono text-xs">
                {latest.temporalWorkflowId}
              </span>
            )}
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">This workflow has never run.</p>
        )}
      </Section>

      <Section
        title="Trigger"
        description="How this workflow starts."
        action={
          entitlement.canManage ? (
            <Button variant="outline" onClick={handleToggle} disabled={toggling}>
              {toggling ? (
                <Loader2Icon className="mr-1 size-4 animate-spin" />
              ) : workflow.isEnabled ? (
                <PowerOffIcon className="mr-1 size-4" />
              ) : (
                <PowerIcon className="mr-1 size-4" />
              )}
              {workflow.isEnabled ? "Disable" : "Enable"}
            </Button>
          ) : null
        }
      >
        <dl className="grid gap-x-8 gap-y-2 text-sm sm:grid-cols-[auto_1fr]">
          <dt className="text-muted-foreground">Trigger kind</dt>
          <dd>
            <Badge variant="outline">{formatTriggerKind(workflow.triggerKind)}</Badge>
          </dd>
          {workflow.scheduleCron && (
            <>
              <dt className="text-muted-foreground">Schedule</dt>
              <dd className="font-mono text-xs">{workflow.scheduleCron}</dd>
            </>
          )}
          <dt className="text-muted-foreground">Status</dt>
          <dd>
            <Badge variant={workflow.isEnabled ? "default" : "secondary"}>
              {workflow.isEnabled ? "Enabled" : "Disabled"}
            </Badge>
          </dd>
        </dl>
      </Section>
    </div>
  );
}
