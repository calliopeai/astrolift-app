"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertTriangleIcon,
  LayoutTemplateIcon,
  Loader2Icon,
  PlusIcon,
} from "lucide-react";
import { toast } from "sonner";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { AgentWorkloadPicker } from "@/components/workflows/pickers";
import { useAgentWorkloadOptions } from "@/components/workflows/use-picker-options";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import {
  useCreateConfiguredWorkflow,
  useWorkflowDefinition,
  useWorkflowStages,
  useWorkflowsEntitlement,
} from "@/graphql/workflows/tiered.hooks";

// ─── Configure-to-run form (?definition=<slug>) ──────────────────────────────

const STAGE_KIND_LABEL: Record<string, string> = {
  agent_dispatch: "Agent",
  human_gate: "Human gate",
  aggregation: "Aggregation",
  checkpoint: "Checkpoint",
};

function ConfigureFromDefinition({ definitionSlug }: { definitionSlug: string }) {
  const router = useRouter();
  const { org } = useActiveOrg();
  const orgScoped = org?.id ?? null;
  const agentWorkloads = useAgentWorkloadOptions(orgScoped);
  const { canCreate, loading: entitlementLoading } = useWorkflowsEntitlement();
  const { definition, loading: definitionLoading, error } =
    useWorkflowDefinition(definitionSlug);
  const { stages, loading: stagesLoading } = useWorkflowStages(definitionSlug);
  const [createWorkflow, { loading: submitting }] = useCreateConfiguredWorkflow();

  const [name, setName] = useState("");
  const [bindings, setBindings] = useState<Record<number, string | null>>({});
  const [inputsText, setInputsText] = useState("");
  const [triggerKind, setTriggerKind] = useState<"manual" | "schedule">("manual");
  const [scheduleCron, setScheduleCron] = useState("");

  const loading = definitionLoading || stagesLoading;
  const orderedStages = [...stages].sort((a, b) => a.order - b.order);
  // An agent_dispatch stage is bound when the operator picked a workload here
  // or the definition's stage carries its own default agent.
  const unboundStages = orderedStages.filter(
    (s) => s.kind === "agent_dispatch" && !bindings[s.order] && !s.agentDefinitionGuid
  );

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
    if (!name.trim()) {
      toast.error("Name is required");
      return;
    }
    if (unboundStages.length > 0) return;

    let inputs: Record<string, unknown> | undefined;
    if (inputsText.trim()) {
      try {
        inputs = JSON.parse(inputsText);
      } catch {
        toast.error("Inputs must be valid JSON");
        return;
      }
    }

    const stageBindings: Record<string, { agent_workload_id: string }> = {};
    for (const [order, workloadId] of Object.entries(bindings)) {
      if (workloadId) stageBindings[order] = { agent_workload_id: workloadId };
    }

    const { data } = await createWorkflow({
      variables: {
        name: name.trim(),
        definitionSlug,
        stageBindings,
        inputs,
        triggerKind,
        scheduleCron: triggerKind === "schedule" ? scheduleCron : undefined,
      },
    });

    const result = data?.createWorkflow;
    if (result?.ok && result.workflow) {
      toast.success("Workflow created", {
        description: `${result.workflow.name} is configured and ready to run.`,
      });
      router.push(`/workflows/${result.workflow.slug}`);
    } else {
      const errors = result?.errors ?? [];
      if (errors.length > 0) {
        for (const err of errors) {
          toast.error(`${err.field}: ${err.messages.join(", ")}`);
        }
      } else {
        toast.error("Failed to create workflow");
      }
    }
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
              Bind a registered agent to each agent stage. Stages with a default
              agent run it unless you pick an override.
            </p>
          </div>
          {orderedStages.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              This template has no stages yet.
            </p>
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
            <Select
              value={triggerKind}
              onValueChange={(v) => setTriggerKind(v as "manual" | "schedule")}
            >
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

// ─── Page ─────────────────────────────────────────────────────────────────────

function NewWorkflowPageInner() {
  const router = useRouter();
  const definitionSlug = useSearchParams().get("definition");

  // Without ?definition= there is nothing to configure here — the
  // pattern-picker / manifest-import flow at /workflows/builder is the
  // way to author a new definition.
  useEffect(() => {
    if (!definitionSlug) {
      router.replace("/workflows/builder");
    }
  }, [definitionSlug, router]);

  if (!definitionSlug) return null;
  return <ConfigureFromDefinition definitionSlug={definitionSlug} />;
}

export default function NewWorkflowPage() {
  return (
    <Suspense fallback={null}>
      <NewWorkflowPageInner />
    </Suspense>
  );
}
