"use client";

import Link from "next/link";
import { useState } from "react";
import { AlertTriangleIcon, LayoutTemplateIcon, Loader2Icon, PlusIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { AgentWorkloadPicker } from "@/components/workflows/pickers";

import {
  findUnboundStages,
  type TriggerKind,
  type useConfigureWorkflow,
} from "./use-configure-workflow";

export type ConfigureWorkflowScreenProps = ReturnType<typeof useConfigureWorkflow>;

const STAGE_KIND_LABEL: Record<string, string> = {
  agent_dispatch: "Agent",
  human_gate: "Human gate",
  aggregation: "Aggregation",
  checkpoint: "Checkpoint",
};

/** Configure a workflow definition (template) into a runnable workflow. */
export function ConfigureWorkflowScreen({
  definitionSlug,
  definition,
  orderedStages,
  loading,
  error,
  canCreate,
  entitlementLoading,
  orgScoped,
  agentWorkloads,
  submitting,
  onSubmit,
}: ConfigureWorkflowScreenProps) {
  const [name, setName] = useState("");
  const [bindings, setBindings] = useState<Record<number, string | null>>({});
  const [inputsText, setInputsText] = useState("");
  const [triggerKind, setTriggerKind] = useState<TriggerKind>("manual");
  const [scheduleCron, setScheduleCron] = useState("");

  const unboundStages = findUnboundStages(orderedStages, bindings);

  if (loading && !definition) {
    return (
      <PageShell title="Create workflow">
        <div className="flex max-w-2xl flex-col gap-4">
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-9 w-40" />
        </div>
      </PageShell>
    );
  }

  if (error || !definition) {
    return (
      <PageShell title="Create workflow">
        <EmptyState
          icon={<LayoutTemplateIcon className="size-5" />}
          title={error ? "Couldn't load this template" : "Template not found"}
          description={
            error
              ? error.message
              : `The workflow definition "${definitionSlug}" may have been deleted, or you may not have access to it.`
          }
          actionHref="/workflows/templates"
          actionLabel="Back to templates"
        />
      </PageShell>
    );
  }

  if (!entitlementLoading && !canCreate) {
    return (
      <PageShell title="Create workflow">
        <EmptyState
          icon={<LayoutTemplateIcon className="size-5" />}
          title="No permission to create workflows"
          description="Your role does not include workflow creation. Ask an organization admin for access."
          actionHref="/workflows/templates"
          actionLabel="Back to templates"
        />
      </PageShell>
    );
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    await onSubmit({ name, bindings, inputsText, triggerKind, scheduleCron });
  };

  return (
    <PageShell
      title={`Create workflow from "${definition.name}"`}
      description={definition.description || "Configure this template into a runnable workflow."}
      actions={
        <Button asChild variant="outline" size="sm">
          <Link href="/workflows/templates">← Templates</Link>
        </Button>
      }
    >
      <form onSubmit={handleSubmit} className="flex max-w-2xl flex-col gap-6">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="wf-name">Workflow name</Label>
          <Input
            id="wf-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={`e.g. ${definition.name} — production`}
            required
          />
        </div>

        <div className="flex flex-col gap-3">
          <div>
            <Label>Stages</Label>
            <p className="text-muted-foreground mt-1 text-sm">
              Bind a registered agent to each agent stage. Stages with a default agent run it unless
              you pick an override.
            </p>
          </div>
          {orderedStages.length === 0 ? (
            <p className="text-muted-foreground text-sm">This template has no stages yet.</p>
          ) : (
            <ol className="flex flex-col gap-3">
              {orderedStages.map((stage) => (
                <li key={stage.guid} className="flex flex-col gap-1.5">
                  <div className="flex items-center gap-2">
                    <span className="text-muted-foreground text-sm tabular-nums">
                      {stage.order}.
                    </span>
                    <Badge variant="outline" className="text-xs">
                      {STAGE_KIND_LABEL[stage.kind] ?? stage.kind}
                    </Badge>
                    {stage.kind === "agent_dispatch" && stage.agentDefinitionName && (
                      <span className="text-muted-foreground text-xs">
                        default: {stage.agentDefinitionName}
                      </span>
                    )}
                  </div>
                  {stage.kind === "agent_dispatch" && (
                    <AgentWorkloadPicker
                      value={bindings[stage.order] ?? null}
                      onChange={(workloadId) =>
                        setBindings((prev) => ({ ...prev, [stage.order]: workloadId }))
                      }
                      orgScoped={orgScoped}
                      {...agentWorkloads}
                    />
                  )}
                </li>
              ))}
            </ol>
          )}
        </div>

        <div className="flex flex-col gap-1.5">
          <Label htmlFor="wf-inputs">Inputs (JSON, optional)</Label>
          <Textarea
            id="wf-inputs"
            value={inputsText}
            onChange={(e) => setInputsText(e.target.value)}
            placeholder='{ "key": "value" }'
            rows={4}
            className="font-mono text-sm"
          />
          <p className="text-muted-foreground text-xs">
            Workflow-level defaults, merged with per-trigger inputs at run time.
          </p>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="flex flex-col gap-1.5">
            <Label>Trigger</Label>
            <Select value={triggerKind} onValueChange={(v) => setTriggerKind(v as TriggerKind)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="manual">Manual</SelectItem>
                <SelectItem value="schedule">Schedule</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {triggerKind === "schedule" && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="wf-cron">Cron expression</Label>
              <Input
                id="wf-cron"
                value={scheduleCron}
                onChange={(e) => setScheduleCron(e.target.value)}
                placeholder="0 9 * * 1-5"
                required
              />
            </div>
          )}
        </div>

        {unboundStages.length > 0 && (
          <div className="border-destructive/50 text-destructive flex items-start gap-2 rounded-md border p-3 text-sm">
            <AlertTriangleIcon className="mt-0.5 h-4 w-4 shrink-0" />
            <p>
              Bind an agent to every agent stage before creating:{" "}
              {unboundStages.map((s) => `stage ${s.order}`).join(", ")}.
            </p>
          </div>
        )}

        <Button
          type="submit"
          disabled={submitting || unboundStages.length > 0}
          className="self-start"
        >
          {submitting ? (
            <Loader2Icon className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <PlusIcon className="mr-2 h-4 w-4" />
          )}
          Create workflow
        </Button>
      </form>
    </PageShell>
  );
}
