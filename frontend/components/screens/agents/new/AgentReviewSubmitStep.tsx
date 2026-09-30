"use client";

import { AlertCircleIcon, CheckCircle2Icon, Loader2Icon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type {
  AstroliftDiscoveredAgentManifest,
  AstroliftRegisteredAgent,
} from "@/graphql/agents/agents.types";
import type { SourceKind } from "@/graphql/registry/registry.types";
import { cn } from "@/lib/utils";

import {
  SummaryCard,
  CheckboxLocked,
  errorFor,
  statusFor,
  StatusBadge,
} from "@/components/wizard/ReviewParts";
import type { SideEffectStep } from "@/components/wizard/ReviewParts";
export type { SideEffectStep, StepStatus } from "@/components/wizard/ReviewParts";

/** The agent-wizard-state fields the review step reads. */
export interface AgentReviewSubmitFields {
  sourceRepo: string;
  ref: string;
  defaultBranch: string;
  sourceKind: SourceKind;
  discoveredAgents: AstroliftDiscoveredAgentManifest[];
  projectId: string;
}

export interface AgentReviewSubmitStepViewProps {
  state: AgentReviewSubmitFields;
  // Human label for the picked project (`team/project`), resolved by the
  // wizard from the project list — agents have no name/slug of their own here.
  projectLabel: string;
  /** Back to a step to edit it: 1 Source (repo and agents found), 2 Configure (project). */
  onJumpToStep: (step: 1 | 2) => void;
  submitting: boolean;
  submitError: string | null;
  sideEffects: SideEffectStep[];
  // Per-agent registration outcome, populated after a successful submit.
  registeredAgents: AstroliftRegisteredAgent[];
}

/** New agent, Review: summary, per-agent outcome and the submit side effects. */
export function AgentReviewSubmitStepView({
  state,
  projectLabel,
  onJumpToStep,
  submitting,
  submitError,
  sideEffects,
  registeredAgents,
}: AgentReviewSubmitStepViewProps) {
  const newAgents = state.discoveredAgents.filter((a) => !a.alreadyRegistered);
  const alreadyCount = state.discoveredAgents.length - newAgents.length;

  return (
    <div className="flex flex-col gap-5">
      <section className="grid gap-3 sm:grid-cols-2">
        <SummaryCard
          step={1}
          title="Repository"
          onJump={onJumpToStep}
          rows={[
            { label: "Repository", value: state.sourceRepo, mono: true },
            { label: "Branch / ref", value: state.ref || state.defaultBranch, mono: true },
            { label: "Source kind", value: state.sourceKind },
          ]}
        />
        <SummaryCard
          step={1}
          title="Agents found"
          onJump={onJumpToStep}
          rows={[
            { label: "Discovered", value: `${state.discoveredAgents.length} agent(s)` },
            { label: "New", value: `${newAgents.length}` },
            { label: "Already registered", value: `${alreadyCount}` },
          ]}
        />
        <SummaryCard
          step={2}
          title="Project"
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
                    <span className="text-success-fg inline-flex items-center gap-1 text-xs">
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
