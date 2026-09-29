"use client";

import { Loader2Icon, PlayIcon, PowerIcon, PowerOffIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { useFormatters } from "@/lib/i18n/formatters";

import { RunStateBadge } from "./RunStateBadge";
import type { WorkflowRunState } from "./use-workflow-run";
import { formatTriggerKind } from "./workflow-run-state";

export type WorkflowRunScreenProps = WorkflowRunState;

/**
 * Run pillar — dispatch-now (gated `canRun`), the trigger configuration
 * summary with an enable/disable toggle (gated `canManage`), and the latest
 * run's status. All affordances render exactly what `me.modules` answered —
 * no client permission math.
 */
export function WorkflowRunScreen({
  workflow,
  latest,
  canRun,
  canManage,
  running,
  toggling,
  onRun,
  onToggle,
}: WorkflowRunScreenProps) {
  const fmt = useFormatters();

  return (
    <div className="flex flex-col gap-8">
      <Section
        title="Run now"
        description="Dispatch a run of this workflow immediately."
        action={
          canRun ? (
            <Button onClick={onRun} disabled={running || !workflow.isEnabled}>
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
        {!workflow.isEnabled && canRun && (
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
          canManage ? (
            <Button variant="outline" onClick={onToggle} disabled={toggling}>
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
