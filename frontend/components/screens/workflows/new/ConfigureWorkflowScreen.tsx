"use client";

import Link from "next/link";
import * as React from "react";
import {
  AlertTriangleIcon,
  ArrowLeftIcon,
  ArrowRightIcon,
  LayoutTemplateIcon,
  Loader2Icon,
} from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { FlowSteps } from "@/components/screens/agents/skills/FlowSteps";
import { workflowsCrumbs } from "@/components/screens/workflows/list/workflows-list";
import { ShellHeader } from "@/components/shell/ShellHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
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
  type ConfigureErrors,
  type ConfigureWorkflowValues,
  type TriggerKind,
  type useConfigureWorkflow,
  validateConfigure,
} from "./use-configure-workflow";

export type ConfigureWorkflowScreenProps = ReturnType<typeof useConfigureWorkflow> & {
  /** Start on a step, with values or errors; stories use them. */
  initialStep?: Step;
  initialValues?: Partial<ConfigureWorkflowValues>;
  initialErrors?: ConfigureErrors;
};

type Step = 1 | 2 | 3;

const STEPS = [{ label: "Source" }, { label: "Stages" }, { label: "Review" }];

const STAGE_KIND_LABEL: Record<string, string> = {
  agent_dispatch: "Agent",
  human_gate: "Human gate",
  aggregation: "Aggregation",
  checkpoint: "Checkpoint",
  workflow: "Nested workflow",
};

function ReviewRow({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <div className="grid min-w-0 gap-1 sm:grid-cols-[10rem_minmax(0,1fr)]">
      <dt className="text-muted-foreground text-sm">{term}</dt>
      <dd className="min-w-0 text-sm [overflow-wrap:anywhere]">{children}</dd>
    </div>
  );
}

function Header({ step }: { step?: Step }) {
  return (
    <ShellHeader
      crumbs={workflowsCrumbs({ label: "Configure workflow" })}
      title="Configure workflow"
      context={step ? <FlowSteps steps={STEPS} current={step} /> : undefined}
    />
  );
}

/**
 * Agents › Workflows › Configure workflow: a definition (usually a platform
 * template) made into a runnable workflow, a page in three steps (spec 44
 * §5.4). Source is the template, with the workflow's name, trigger and
 * inputs; Stages binds an agent to each agent stage; Review says what will be
 * created. Errors stand beside their fields, and a refusal sends the page
 * back to the step that holds the field; the outcome is the hook's toast.
 * Holds the field values; useConfigureWorkflow creates the workflow.
 */
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
  initialStep = 1,
  initialValues = {},
  initialErrors = {},
}: ConfigureWorkflowScreenProps) {
  const [step, setStep] = React.useState<Step>(initialStep);
  const [values, setValues] = React.useState<ConfigureWorkflowValues>({
    name: "",
    bindings: {},
    inputsText: "",
    triggerKind: "manual",
    scheduleCron: "",
    ...initialValues,
  });
  const [errors, setErrors] = React.useState<ConfigureErrors>(initialErrors);

  const patch = (next: Partial<ConfigureWorkflowValues>, clear: keyof ConfigureErrors) => {
    setValues((v) => ({ ...v, ...next }));
    setErrors((e) => ({ ...e, [clear]: undefined, form: undefined }));
  };

  if (loading && !definition) {
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        <Header />
        <div className="flex max-w-3xl flex-col gap-4">
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-9 w-40" />
        </div>
      </div>
    );
  }

  if (error || !definition) {
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        <Header />
        <EmptyState
          icon={<LayoutTemplateIcon className="size-5" />}
          title={error ? "Couldn't load this template" : "Template not found"}
          description={
            error
              ? error.message
              : `The workflow definition "${definitionSlug}" may have been deleted, or you may not have access to it.`
          }
          actionHref="/workflows?view=templates"
          actionLabel="Back to templates"
        />
      </div>
    );
  }

  if (!entitlementLoading && !canCreate) {
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        <Header />
        <EmptyState
          icon={<LayoutTemplateIcon className="size-5" />}
          title="No permission to create workflows"
          description="Your role does not include workflow creation. Ask an organization admin for access."
          actionHref="/workflows?view=templates"
          actionLabel="Back to templates"
        />
      </div>
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (step < 3) {
      const found = validateConfigure(step === 1 ? "source" : "stages", values, orderedStages);
      setErrors(found);
      if (Object.keys(found).length === 0) setStep((s) => (s + 1) as Step);
      return;
    }
    const found = await onSubmit(values);
    setErrors(found);
    if (found.name || found.scheduleCron || found.inputs) setStep(1);
    else if (found.stages) setStep(2);
  }

  const agentStages = orderedStages.filter((s) => s.kind === "agent_dispatch");
  const workloadName = (id: string | null | undefined) =>
    agentWorkloads.workloads.find((w) => w.id === id)?.name ?? null;

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <Header step={step} />

      <form
        onSubmit={submit}
        noValidate
        className="bg-card flex max-w-3xl min-w-0 flex-col gap-5 rounded-md border p-6"
      >
        {errors.form && (
          <div
            role="alert"
            className="border-destructive/40 bg-destructive/5 text-destructive flex min-w-0 items-start gap-2 rounded-md border p-3 text-sm"
          >
            <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" aria-hidden />
            <span className="min-w-0 [overflow-wrap:anywhere]">{errors.form}</span>
          </div>
        )}

        {step === 1 && (
          <>
            <div className="bg-muted/30 flex min-w-0 flex-col gap-1 rounded-md border p-4">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="min-w-0 font-medium [overflow-wrap:anywhere]">
                  {definition.name}
                </span>
                <Badge variant="outline">{definition.patternKind || "pipeline"}</Badge>
                <span className="text-muted-foreground font-mono text-xs">
                  {orderedStages.length} {orderedStages.length === 1 ? "stage" : "stages"}
                </span>
              </div>
              {definition.description && (
                <p className="text-muted-foreground text-sm [overflow-wrap:anywhere]">
                  {definition.description}
                </p>
              )}
              <Link
                href={`/workflows/${encodeURIComponent(definition.slug)}`}
                className="text-muted-foreground hover:text-foreground w-fit text-xs underline"
              >
                Open the template in the Builder
              </Link>
            </div>

            <Field data-invalid={Boolean(errors.name) || undefined} className="min-w-0">
              <FieldLabel htmlFor="wf-name">Workflow name</FieldLabel>
              <Input
                id="wf-name"
                value={values.name}
                onChange={(e) => patch({ name: e.target.value }, "name")}
                placeholder={`e.g. ${definition.name}, production`}
                aria-invalid={Boolean(errors.name) || undefined}
              />
              <FieldError className="[overflow-wrap:anywhere]">{errors.name}</FieldError>
            </Field>

            <div className="grid min-w-0 gap-4 sm:grid-cols-2">
              <Field className="min-w-0">
                <FieldLabel>Trigger</FieldLabel>
                <Select
                  value={values.triggerKind}
                  onValueChange={(v) => patch({ triggerKind: v as TriggerKind }, "scheduleCron")}
                >
                  <SelectTrigger aria-label="Trigger">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="manual">Manual</SelectItem>
                    <SelectItem value="schedule">Schedule</SelectItem>
                  </SelectContent>
                </Select>
              </Field>
              {values.triggerKind === "schedule" && (
                <Field data-invalid={Boolean(errors.scheduleCron) || undefined} className="min-w-0">
                  <FieldLabel htmlFor="wf-cron">Cron expression</FieldLabel>
                  <Input
                    id="wf-cron"
                    value={values.scheduleCron}
                    onChange={(e) => patch({ scheduleCron: e.target.value }, "scheduleCron")}
                    placeholder="0 9 * * 1-5"
                    className="font-mono"
                    aria-invalid={Boolean(errors.scheduleCron) || undefined}
                  />
                  <FieldError className="[overflow-wrap:anywhere]">
                    {errors.scheduleCron}
                  </FieldError>
                </Field>
              )}
            </div>

            <Field data-invalid={Boolean(errors.inputs) || undefined} className="min-w-0">
              <FieldLabel htmlFor="wf-inputs">Inputs (JSON, optional)</FieldLabel>
              <Textarea
                id="wf-inputs"
                value={values.inputsText}
                onChange={(e) => patch({ inputsText: e.target.value }, "inputs")}
                placeholder='{ "key": "value" }'
                rows={4}
                className="max-h-64 font-mono text-sm"
                aria-invalid={Boolean(errors.inputs) || undefined}
              />
              <FieldDescription>
                Workflow-level defaults, merged with per-trigger inputs at run time.
              </FieldDescription>
              <FieldError className="[overflow-wrap:anywhere]">{errors.inputs}</FieldError>
            </Field>
          </>
        )}

        {step === 2 && (
          <div className="flex min-w-0 flex-col gap-3">
            <p className="text-muted-foreground text-sm">
              Bind a registered agent to each agent stage. Stages with a default agent run it unless
              you pick an override.
            </p>
            {orderedStages.length === 0 ? (
              <p className="text-muted-foreground text-sm">This template has no stages yet.</p>
            ) : (
              <ol className="flex min-w-0 flex-col gap-3">
                {orderedStages.map((stage) => {
                  const stageError = errors.stages?.[stage.order];
                  return (
                    <li key={stage.guid} className="flex min-w-0 flex-col gap-1.5">
                      <div className="flex min-w-0 flex-wrap items-center gap-2">
                        <span className="text-muted-foreground font-mono text-sm tabular-nums">
                          {stage.order}.
                        </span>
                        <Badge variant="outline" className="text-xs">
                          {STAGE_KIND_LABEL[stage.kind] ?? stage.kind}
                        </Badge>
                        {stage.role && (
                          <span className="min-w-0 truncate text-sm" title={stage.role}>
                            {stage.role}
                          </span>
                        )}
                        {stage.kind === "agent_dispatch" && stage.agentDefinitionName && (
                          <span className="text-muted-foreground min-w-0 truncate text-xs">
                            default: {stage.agentDefinitionName}
                          </span>
                        )}
                      </div>
                      {stage.kind === "agent_dispatch" && (
                        <AgentWorkloadPicker
                          value={values.bindings[stage.order] ?? null}
                          onChange={(workloadId) =>
                            patch(
                              { bindings: { ...values.bindings, [stage.order]: workloadId } },
                              "stages"
                            )
                          }
                          orgScoped={orgScoped}
                          {...agentWorkloads}
                        />
                      )}
                      {stageError && (
                        <p role="alert" className="text-destructive text-sm">
                          {stageError}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ol>
            )}
          </div>
        )}

        {step === 3 && (
          <dl className="flex min-w-0 flex-col gap-3">
            <ReviewRow term="Name">{values.name}</ReviewRow>
            <ReviewRow term="Template">{definition.name}</ReviewRow>
            <ReviewRow term="Trigger">
              {values.triggerKind === "schedule" ? (
                <>
                  Schedule <span className="font-mono">{values.scheduleCron}</span>
                </>
              ) : (
                "Manual"
              )}
            </ReviewRow>
            <ReviewRow term="Agents">
              {agentStages.length === 0 ? (
                "No agent stages"
              ) : (
                <ul className="flex min-w-0 flex-col gap-1">
                  {agentStages.map((s) => (
                    <li key={s.guid} className="min-w-0">
                      <span className="text-muted-foreground font-mono">{s.order}.</span>{" "}
                      {workloadName(values.bindings[s.order]) ??
                        (s.agentDefinitionName ? `${s.agentDefinitionName} (default)` : "unbound")}
                    </li>
                  ))}
                </ul>
              )}
            </ReviewRow>
            <ReviewRow term="Inputs">
              {values.inputsText.trim() ? (
                <pre className="bg-muted/40 max-h-40 overflow-auto rounded-md border p-2 font-mono text-xs">
                  {values.inputsText}
                </pre>
              ) : (
                "none"
              )}
            </ReviewRow>
          </dl>
        )}

        <div className="flex min-w-0 flex-wrap items-center justify-end gap-2 border-t pt-4">
          <Button type="button" variant="ghost" asChild className="mr-auto">
            <Link href="/workflows?view=templates">Cancel</Link>
          </Button>
          {step > 1 && (
            <Button type="button" variant="outline" onClick={() => setStep((s) => (s - 1) as Step)}>
              <ArrowLeftIcon className="size-4" />
              Back
            </Button>
          )}
          {step < 3 ? (
            <Button type="submit">
              Continue
              <ArrowRightIcon className="size-4" />
            </Button>
          ) : (
            <Button type="submit" disabled={submitting}>
              {submitting && <Loader2Icon className="size-4 animate-spin" />}
              Create workflow
            </Button>
          )}
        </div>
      </form>
    </div>
  );
}
