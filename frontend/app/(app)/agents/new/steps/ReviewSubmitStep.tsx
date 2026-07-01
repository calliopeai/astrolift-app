"use client";

import {
  AlertCircleIcon,
  CheckCircle2Icon,
  CircleDashedIcon,
  EditIcon,
  Loader2Icon,
} from "lucide-react";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { AstroliftRegisteredAgent } from "@/graphql/agents/agents.types";
import { cn } from "@/lib/utils";

import type { WizardState } from "../wizard-client";
import type { WizardStep } from "../components/WizardShell";

export type StepStatus = "pending" | "running" | "done" | "failed" | "skipped";

export interface SideEffectStep {
  key: string;
  label: string;
  status: StepStatus;
  error?: string;
  note?: string;
}

interface Props {
  state: WizardState;
  // Human label for the picked project (`team/project`), resolved by the
  // wizard from the project list — agents have no name/slug of their own here.
  projectLabel: string;
  steps: WizardStep[];
  onJumpToStep: (idx: number) => void;
  submitting: boolean;
  submitError: string | null;
  sideEffects: SideEffectStep[];
  // Per-agent registration outcome, populated after a successful submit.
  registeredAgents: AstroliftRegisteredAgent[];
}

export function ReviewSubmitStep({
  state,
  projectLabel,
  steps,
  onJumpToStep,
  submitting,
  submitError,
  sideEffects,
  registeredAgents,
}: Props) {
  const newAgents = state.discoveredAgents.filter((a) => !a.alreadyRegistered);
  const alreadyCount = state.discoveredAgents.length - newAgents.length;

  return (
    <div className="flex flex-col gap-5">
      <section className="grid gap-3 sm:grid-cols-2">
        <SummaryCard
          stepIdx={1}
          title={steps[0].label}
          onJump={onJumpToStep}
          rows={[
            { label: "Repository", value: state.sourceRepo, mono: true },
            { label: "Branch / ref", value: state.ref || state.defaultBranch, mono: true },
            { label: "Source kind", value: state.sourceKind },
          ]}
        />
        <SummaryCard
          stepIdx={2}
          title={steps[1].label}
          onJump={onJumpToStep}
          rows={[
            { label: "Discovered", value: `${state.discoveredAgents.length} agent(s)` },
            { label: "New", value: `${newAgents.length}` },
            { label: "Already registered", value: `${alreadyCount}` },
          ]}
        />
        <SummaryCard
          stepIdx={3}
          title={steps[2].label}
          onJump={onJumpToStep}
          rows={[{ label: "Project", value: projectLabel || state.projectId, mono: !projectLabel }]}
        />
      </section>

      <section className="flex flex-col gap-3">
        <div>
          <h3 className="font-medium">Agents to register</h3>
          <p className="text-muted-foreground text-xs">
            Registering the repo creates every new agent below. Already-registered agents are
            matched (not duplicated). The backend registers per repo — there is no per-agent filter.
          </p>
        </div>
        <ul className="flex flex-col divide-y rounded-md border">
          {state.discoveredAgents.map((a) => {
            const outcome = registeredAgents.find((r) => r.manifestPath === a.manifestPath);
            return (
              <li
                key={a.manifestPath}
                className={cn("flex items-center gap-3 p-3", a.alreadyRegistered && "opacity-70")}
              >
                <div className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate text-sm font-medium">{a.name}</span>
                  <span className="text-muted-foreground truncate font-mono text-xs">
                    {a.manifestPath} · {a.slug}
                  </span>
                </div>
                {outcome ? (
                  outcome.created ? (
                    <span className="inline-flex items-center gap-1 text-xs text-success-fg">
                      <CheckCircle2Icon className="size-3.5" /> Created
                    </span>
                  ) : (
                    <Badge variant="outline" className="text-2xs">
                      Already existed
                    </Badge>
                  )
                ) : a.alreadyRegistered ? (
                  <Badge variant="secondary" className="text-2xs">
                    Already registered
                  </Badge>
                ) : (
                  <Badge variant="outline" className="text-2xs">
                    Will register
                  </Badge>
                )}
              </li>
            );
          })}
        </ul>
      </section>

      <section className="flex flex-col gap-3">
        <div>
          <h3 className="font-medium">What happens when you submit</h3>
          <p className="text-muted-foreground text-xs">
            The register step calls{" "}
            <code className="bg-muted rounded px-1 py-0.5 font-mono">registerAgentRepo</code>. Items
            that depend on backend work that hasn&apos;t shipped yet stay grayed out.
          </p>
        </div>

        <ul className="flex flex-col divide-y rounded-md border">
          <li className="flex items-center gap-3 p-3">
            <CheckboxLocked checked disabled />
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="text-sm">Register the repo&apos;s agents</span>
              <span className="text-muted-foreground text-xs">
                Calls{" "}
                <code className="bg-muted rounded px-1 py-0.5 font-mono">registerAgentRepo</code>;
                each agent manifest becomes an agent workload under its own app.
              </span>
            </div>
            <StatusBadge
              status={statusFor(sideEffects, "register")}
              error={errorFor(sideEffects, "register")}
            />
          </li>

          <li className="flex items-center gap-3 p-3 opacity-70">
            <CheckboxLocked checked={false} disabled />
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="text-sm">Push CI workflow</span>
              <span className="text-muted-foreground text-xs">
                Agents are dispatched on demand, not deployed on push, so the wizard doesn&apos;t
                commit a deploy-on-push workflow. Configure CI per agent from its app page if
                needed.
              </span>
            </div>
            <Badge variant="outline" className="text-2xs">
              Not applicable
            </Badge>
          </li>
        </ul>
      </section>

      {submitError && (
        <div className="border-destructive/40 bg-destructive/10 rounded-md border p-3 text-sm">
          <p className="text-destructive font-medium">
            <AlertCircleIcon className="mr-1 inline size-4" />
            Submit failed
          </p>
          <p className="text-destructive/80 mt-1 text-xs">{submitError}</p>
        </div>
      )}

      {submitting && sideEffects.length === 0 && (
        <p className="text-muted-foreground inline-flex items-center gap-2 text-xs">
          <Loader2Icon className="size-3.5 animate-spin" />
          Submitting…
        </p>
      )}
    </div>
  );
}

function SummaryCard({
  stepIdx,
  title,
  onJump,
  rows,
}: {
  stepIdx: number;
  title: string;
  onJump: (idx: number) => void;
  rows: Array<{ label: string; value: string; mono?: boolean }>;
}) {
  return (
    <Card className="border-muted-foreground/20">
      <CardContent className="flex flex-col gap-2 p-4 text-sm">
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
            {title}
          </span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onJump(stepIdx)}
            className="text-muted-foreground hover:text-foreground -mt-1 -mr-2 h-7 gap-1 px-2 text-xs"
            aria-label={`Edit ${title}`}
          >
            <EditIcon className="size-3" />
            Edit
          </Button>
        </div>
        <dl className="grid gap-1">
          {rows.map((r) => (
            <div key={r.label} className="grid grid-cols-3 gap-2">
              <dt className="text-muted-foreground text-xs">{r.label}</dt>
              <dd className={cn("col-span-2 truncate", r.mono && "font-mono text-xs")}>
                {r.value || "—"}
              </dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}

function CheckboxLocked({ checked, disabled }: { checked: boolean; disabled: boolean }) {
  return (
    <input
      type="checkbox"
      checked={checked}
      disabled={disabled}
      readOnly
      className="pointer-events-none"
      aria-disabled={disabled}
    />
  );
}

function statusFor(effects: SideEffectStep[], key: string): StepStatus {
  return effects.find((e) => e.key === key)?.status ?? "pending";
}

function errorFor(effects: SideEffectStep[], key: string): string | undefined {
  return effects.find((e) => e.key === key)?.error;
}

function StatusBadge({ status, error }: { status: StepStatus; error?: string }) {
  if (status === "running") {
    return (
      <span className="text-muted-foreground inline-flex items-center gap-1 text-xs">
        <Loader2Icon className="size-3.5 animate-spin" /> Running
      </span>
    );
  }
  if (status === "done") {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-success-fg">
        <CheckCircle2Icon className="size-3.5" /> Done
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="text-destructive inline-flex items-center gap-1 text-xs" title={error}>
        <AlertCircleIcon className="size-3.5" /> Failed
      </span>
    );
  }
  if (status === "skipped") {
    return (
      <Badge variant="outline" className="text-2xs">
        Skipped
      </Badge>
    );
  }
  return (
    <span className="text-muted-foreground inline-flex items-center gap-1 text-xs">
      <CircleDashedIcon className="size-3.5" /> Pending
    </span>
  );
}
