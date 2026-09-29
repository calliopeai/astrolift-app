"use client";

import { Loader2Icon, PlayIcon, PowerIcon, PowerOffIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import type { ConfiguredWorkflow } from "@/graphql/workflows/tiered.types";

import { formatTriggerKind } from "./workflow-run-state";

export interface WorkflowTriggersViewProps {
  /** A configured workflow's trigger; null for a definition opened directly, which runs on demand. */
  trigger: Pick<ConfiguredWorkflow, "triggerKind" | "scheduleCron" | "isEnabled"> | null;
  /** `workflow.update`: the enable toggle. */
  canManage: boolean;
  toggling: boolean;
  onToggle: () => void;
}

/**
 * The Triggers tab (spec 44 §5.2): how this workflow starts, its schedule,
 * and whether it is enabled, with the enable toggle (gated `canManage`). The
 * trigger section the Run pillar carried, moved here as it was; Run itself
 * is the frame's primary action. A definition has no trigger of its own.
 */
export function WorkflowTriggersView({
  trigger,
  canManage,
  toggling,
  onToggle,
}: WorkflowTriggersViewProps) {
  if (!trigger) {
    return (
      <EmptyState
        icon={<PlayIcon className="size-5" />}
        title="Runs on demand"
        description="A workflow definition has no trigger of its own. Start it with Run, or configure a workflow over it to run on a schedule or a webhook."
        actionHref="/workflows/new"
        actionLabel="Configure a workflow"
      />
    );
  }

  return (
    <Section
      title="Trigger"
      description="How this workflow starts."
      action={
        canManage ? (
          <Button variant="outline" onClick={onToggle} disabled={toggling}>
            {toggling ? (
              <Loader2Icon className="mr-1 size-4 animate-spin" />
            ) : trigger.isEnabled ? (
              <PowerOffIcon className="mr-1 size-4" />
            ) : (
              <PowerIcon className="mr-1 size-4" />
            )}
            {trigger.isEnabled ? "Disable" : "Enable"}
          </Button>
        ) : null
      }
    >
      <dl className="grid min-w-0 gap-x-8 gap-y-2 text-sm sm:grid-cols-[auto_minmax(0,1fr)]">
        <dt className="text-muted-foreground">Trigger kind</dt>
        <dd className="min-w-0">
          <Badge
            variant="outline"
            className="max-w-full [overflow-wrap:anywhere] whitespace-normal"
          >
            {formatTriggerKind(trigger.triggerKind)}
          </Badge>
        </dd>
        {trigger.scheduleCron && (
          <>
            <dt className="text-muted-foreground">Schedule</dt>
            <dd className="min-w-0 font-mono text-xs [overflow-wrap:anywhere]">
              {trigger.scheduleCron}
            </dd>
          </>
        )}
        <dt className="text-muted-foreground">Status</dt>
        <dd>
          <Badge variant={trigger.isEnabled ? "default" : "secondary"}>
            {trigger.isEnabled ? "Enabled" : "Disabled"}
          </Badge>
        </dd>
      </dl>
    </Section>
  );
}
