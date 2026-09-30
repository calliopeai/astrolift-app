"use client";

import { AlertCircleIcon, Loader2Icon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { SourceKind, TriggerMode } from "@/graphql/registry/registry.types";
import { TOPOLOGY_META } from "@/lib/topology";

import { deployPreview } from "./deploy-preview";

import {
  SummaryCard,
  CheckboxLocked,
  errorFor,
  statusFor,
  StatusBadge,
} from "@/components/wizard/ReviewParts";
import type { SideEffectStep } from "@/components/wizard/ReviewParts";
export type { SideEffectStep, StepStatus } from "@/components/wizard/ReviewParts";

/** The wizard-state fields the review step reads. */
export interface ReviewSubmitFields {
  sourceRepo: string;
  defaultBranch: string;
  sourceKind: SourceKind;
  manifestPath: string;
  manifestFromRepo: boolean;
  manifestValid: boolean;
  name: string;
  slug: string;
  description: string;
  deployTiming: string;
  triggerMode: TriggerMode;
  deployBranch: string;
  cronExpression: string;
  requiresApproval: boolean;
  approverUserIds: string[];
  approverTeamId: string;
  minimumApprovals: number;
  manifestRaw: string;
  manifestLater: boolean;
  connectionIsAppInstall: boolean;
  pushCiWorkflow: boolean;
  triggerFirstDeploy: boolean;
}

export interface ReviewSubmitStepViewProps {
  state: ReviewSubmitFields;
  /** Back to step 1 (Source) or 2 (Run) to change something. */
  onJumpToStep: (step: 1 | 2) => void;
  /** The selected connection can push commits (#908). */
  canPushCiWorkflow: boolean;
  onPushCiWorkflowChange: (checked: boolean) => void;
  onTriggerFirstDeployChange: (checked: boolean) => void;
  submitting: boolean;
  submitError: string | null;
  sideEffects: SideEffectStep[];
}

const TRIGGER_LABELS: Record<string, string> = {
  auto_on_push: "Auto on push",
  cron: "Cron schedule",
  manual: "Manual only",
  external_ci: "External CI",
};

const DEPLOY_TIMING_LABELS: Record<string, string> = {
  now: "Deploy now",
  later: "Configure, deploy later",
  skip: "Skip — configure after onboarding",
};

function describeApprovalPolicy(state: ReviewSubmitFields): string {
  if (!state.requiresApproval) return "Not required";
  if (state.approverTeamId) {
    return `Team approval — ${state.minimumApprovals} required`;
  }
  if (state.approverUserIds.length > 0) {
    return `${state.minimumApprovals} of ${state.approverUserIds.length} selected user(s)`;
  }
  return "Required, no approvers selected (fix on Run)";
}

function ciWorkflowPathFor(sourceKind: SourceKind): string {
  if (sourceKind === "gitlab") return ".gitlab-ci.yml";
  return ".github/workflows/astrolift-deploy.yml";
}

/**
 * New app step 3, Review: what Source and Run set, what will deploy, the
 * side-effect checklist, and submit progress.
 */
export function ReviewSubmitStepView({
  state,
  onJumpToStep,
  canPushCiWorkflow,
  onPushCiWorkflowChange,
  onTriggerFirstDeployChange,
  submitting,
  submitError,
  sideEffects,
}: ReviewSubmitStepViewProps) {
  return (
    <div className="flex flex-col gap-5">
      <section className="grid min-w-0 gap-3 sm:grid-cols-2">
        <SummaryCard
          step={1}
          title="Source"
          onJump={onJumpToStep}
          rows={[
            { label: "Repository", value: state.sourceRepo, mono: true },
            { label: "Default branch", value: state.defaultBranch, mono: true },
            { label: "Source kind", value: state.sourceKind },
            { label: "Manifest path", value: state.manifestPath, mono: true },
            {
              label: "Manifest",
              value: state.manifestLater
                ? "Set up later"
                : `${state.manifestFromRepo ? "Loaded from repo" : "Drafted here"}, ${
                    state.manifestValid ? "valid shape" : "invalid"
                  }`,
            },
          ]}
        />
        <SummaryCard
          step={2}
          title="Run"
          onJump={onJumpToStep}
          rows={[
            { label: "Name", value: state.name },
            { label: "Slug", value: state.slug, mono: true },
            ...(state.description ? [{ label: "Description", value: state.description }] : []),
            ...(state.deployTiming === "skip"
              ? [
                  { label: "Deploy timing", value: DEPLOY_TIMING_LABELS[state.deployTiming] },
                  { label: "Trigger", value: "Manual (configure later)" },
                ]
              : [
                  {
                    label: "Deploy timing",
                    value: DEPLOY_TIMING_LABELS[state.deployTiming] ?? state.deployTiming,
                  },
                  {
                    label: "Trigger",
                    value: TRIGGER_LABELS[state.triggerMode] ?? state.triggerMode,
                  },
                  { label: "Deploy branch", value: state.deployBranch, mono: true },
                  ...(state.triggerMode === "cron"
                    ? [{ label: "Cron", value: state.cronExpression, mono: true }]
                    : []),
                  { label: "Approval gate", value: describeApprovalPolicy(state) },
                ]),
          ]}
        />
      </section>

      <WhatWillDeploy state={state} />

      <section className="flex flex-col gap-3">
        <div>
          <h3 className="font-medium">What happens when you submit</h3>
          <p className="text-muted-foreground text-xs">
            Toggle the side-effects you want. Items that depend on backend work that hasn&apos;t
            shipped yet (webhook install for PAT / OAuth-User connections) stay grayed out.
          </p>
        </div>

        <ul className="flex flex-col divide-y rounded-md border">
          <li className="flex items-center gap-3 p-3">
            <CheckboxLocked checked disabled />
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="text-sm">Register the app</span>
              <span className="text-muted-foreground text-xs">
                Calls <code className="bg-muted rounded px-1 py-0.5 font-mono">registerApp</code>;
                the onboarding workflow provisions namespace + registry repo.
              </span>
            </div>
            <StatusBadge
              status={statusFor(sideEffects, "register")}
              error={errorFor(sideEffects, "register")}
            />
          </li>

          <li className="flex items-center gap-3 p-3 opacity-70">
            <CheckboxLocked checked={state.connectionIsAppInstall} disabled />
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="text-sm">Install SCM webhook</span>
              <span className="text-muted-foreground text-xs">
                {state.connectionIsAppInstall
                  ? "Already covered by your GitHub App install — no action needed."
                  : "GitHub App installs handle this automatically; PAT/OAuth-User connections will get a per-repo webhook once that mutation lands."}
              </span>
            </div>
            <Badge variant="outline" className="text-2xs">
              {state.connectionIsAppInstall ? "Already installed" : "Coming soon"}
            </Badge>
          </li>

          <li className="flex items-center gap-3 p-3">
            <label className="flex items-center">
              <input
                type="checkbox"
                checked={state.pushCiWorkflow}
                disabled={!canPushCiWorkflow}
                onChange={(e) => onPushCiWorkflowChange(e.target.checked)}
              />
            </label>
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="text-sm">
                Push{" "}
                <code className="bg-muted rounded px-1 py-0.5 font-mono">
                  {ciWorkflowPathFor(state.sourceKind)}
                </code>
              </span>
              <span className="text-muted-foreground text-xs">
                {canPushCiWorkflow
                  ? "Commits a starter workflow that calls astro app deploy on every push to the deploy branch. You'll need to set ASTROLIFT_DEPLOY_TOKEN as a repo secret."
                  : "Available for GitHub or GitLab user-OAuth connections (and GitHub App installs). The selected connection can't push commits."}
              </span>
            </div>
            <StatusBadge
              status={statusFor(sideEffects, "ci_workflow")}
              error={errorFor(sideEffects, "ci_workflow")}
            />
          </li>

          <li className="flex items-center gap-3 p-3">
            <label className="flex items-center">
              <input
                type="checkbox"
                checked={state.triggerFirstDeploy}
                onChange={(e) => onTriggerFirstDeployChange(e.target.checked)}
              />
            </label>
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="text-sm">Trigger first deploy</span>
              <span className="text-muted-foreground text-xs">
                Kick off an initial deploy once the onboarding workflow flips the app to{" "}
                <code className="bg-muted rounded px-1 py-0.5 font-mono">ready</code>.
              </span>
            </div>
            <StatusBadge
              status={statusFor(sideEffects, "first_deploy")}
              error={errorFor(sideEffects, "first_deploy")}
            />
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

/**
 * What registering will run (spec 44 §5.4): the manifest's workloads and
 * managed services, the app's shape, and whether a first deploy follows.
 */
function WhatWillDeploy({ state }: { state: ReviewSubmitFields }) {
  const preview = state.manifestLater ? null : deployPreview(state.manifestRaw);
  const deploys = state.deployTiming !== "skip" && state.triggerFirstDeploy;
  return (
    <section className="flex min-w-0 flex-col gap-3">
      <div>
        <h3 className="font-medium">What will deploy</h3>
        <p className="text-muted-foreground text-xs">
          {deploys
            ? "A first deploy runs once onboarding lands the app in ready."
            : "Nothing deploys yet: registration only. Start a deploy from the app page."}
        </p>
      </div>
      {state.manifestLater ? (
        <p className="text-muted-foreground rounded-md border border-dashed p-3 text-xs">
          No manifest yet, so there is nothing to deploy until you add one on the app&apos;s
          Manifest tab.
        </p>
      ) : !preview || preview.workloads.length === 0 ? (
        <p className="text-muted-foreground rounded-md border border-dashed p-3 text-xs">
          The manifest declares no workloads Astrolift can read here. The server validates it on
          create.
        </p>
      ) : (
        <div className="min-w-0 rounded-md border">
          <div className="flex min-w-0 flex-wrap items-center gap-2 border-b p-3 text-sm">
            <span className="font-medium">{TOPOLOGY_META[preview.topology].label}</span>
            <span className="text-muted-foreground text-xs">
              {TOPOLOGY_META[preview.topology].blurb}
            </span>
          </div>
          <ul className="divide-y">
            {preview.workloads.map((w, i) => (
              <li key={`${w.name}-${i}`} className="flex min-w-0 items-center gap-3 p-3 text-sm">
                <span className="min-w-0 flex-1 truncate font-mono text-xs" title={w.name}>
                  {w.name}
                </span>
                <Badge variant="outline" className="shrink-0 font-mono">
                  {w.kind}
                </Badge>
                {w.replicas !== null && (
                  <span className="text-muted-foreground shrink-0 font-mono text-xs">
                    x{w.replicas}
                  </span>
                )}
              </li>
            ))}
            {preview.services.map((svc, i) => (
              <li key={`${svc}-${i}`} className="flex min-w-0 items-center gap-3 p-3 text-sm">
                <span className="text-muted-foreground min-w-0 flex-1 truncate text-xs">
                  Managed service
                </span>
                <Badge variant="secondary" className="shrink-0 font-mono">
                  {svc}
                </Badge>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
