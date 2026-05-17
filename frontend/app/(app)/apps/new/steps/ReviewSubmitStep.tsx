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
  setState: React.Dispatch<React.SetStateAction<WizardState>>;
  steps: WizardStep[];
  onJumpToStep: (idx: number) => void;
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

// Connection kinds that hold a usable write-token. OAuth-app config
// rows (kind ends in ``_oauth_app`` with no account_login) carry the
// app's client secret, not a user token, so they can't push commits
// until the OAuth dance produces a sibling ``_oauth_user`` row.
const CI_PUSHABLE_KINDS = new Set<string>([
  "github_oauth_user",
  "github_app_install",
  "github_pat",
  "gitlab_oauth_user",
  "gitlab_pat",
]);

function canPushCiWorkflow(state: WizardState): boolean {
  return CI_PUSHABLE_KINDS.has(state.connectionKind);
}

function describeApprovalPolicy(state: WizardState): string {
  if (!state.requiresApproval) return "Not required";
  if (state.approverTeamId) {
    return `Team approval — ${state.minimumApprovals} required`;
  }
  if (state.approverUserIds.length > 0) {
    return `${state.minimumApprovals} of ${state.approverUserIds.length} selected user(s)`;
  }
  return "Required — no approvers selected (fix on step 4)";
}

function ciWorkflowPathFor(sourceKind: WizardState["sourceKind"]): string {
  if (sourceKind === "gitlab") return ".gitlab-ci.yml";
  return ".github/workflows/astrolift-deploy.yml";
}

export function ReviewSubmitStep({
  state,
  setState,
  steps,
  onJumpToStep,
  submitting,
  submitError,
  sideEffects,
}: Props) {
  return (
    <div className="flex flex-col gap-5">
      <section className="grid gap-3 sm:grid-cols-2">
        <SummaryCard
          stepIdx={1}
          title={steps[0].label}
          onJump={onJumpToStep}
          rows={[
            { label: "Repository", value: state.sourceRepo, mono: true },
            { label: "Default branch", value: state.defaultBranch, mono: true },
            { label: "Source kind", value: state.sourceKind },
          ]}
        />
        <SummaryCard
          stepIdx={2}
          title={steps[1].label}
          onJump={onJumpToStep}
          rows={[
            { label: "Manifest path", value: state.manifestPath, mono: true },
            {
              label: "Source",
              value: state.manifestFromRepo ? "Loaded from repo" : "Drafted in wizard",
            },
            {
              label: "Validity",
              value: state.manifestValid ? "Valid shape" : "Invalid",
            },
          ]}
        />
        <SummaryCard
          stepIdx={3}
          title={steps[2].label}
          onJump={onJumpToStep}
          rows={[
            { label: "Name", value: state.name },
            { label: "Slug", value: state.slug, mono: true },
            ...(state.description ? [{ label: "Description", value: state.description }] : []),
          ]}
        />
        <SummaryCard
          stepIdx={4}
          title={steps[3].label}
          onJump={onJumpToStep}
          rows={
            state.deployTiming === "skip"
              ? [
                  {
                    label: "Deploy timing",
                    value: DEPLOY_TIMING_LABELS[state.deployTiming],
                  },
                  {
                    label: "Trigger",
                    value: "Manual (configure later)",
                  },
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
                  {
                    label: "Deploy branch",
                    value: state.deployBranch,
                    mono: true,
                  },
                  ...(state.triggerMode === "cron"
                    ? [
                        {
                          label: "Cron",
                          value: state.cronExpression,
                          mono: true,
                        },
                      ]
                    : []),
                  {
                    label: "Approval gate",
                    value: describeApprovalPolicy(state),
                  },
                ]
          }
        />
      </section>

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
            <Badge variant="outline" className="text-[10px]">
              {state.connectionIsAppInstall ? "Already installed" : "Coming soon"}
            </Badge>
          </li>

          <li className="flex items-center gap-3 p-3">
            <label className="flex items-center">
              <input
                type="checkbox"
                checked={state.pushCiWorkflow}
                disabled={!canPushCiWorkflow(state)}
                onChange={(e) =>
                  setState((s) => ({
                    ...s,
                    pushCiWorkflow: e.target.checked,
                  }))
                }
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
                {canPushCiWorkflow(state)
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
                onChange={(e) =>
                  setState((s) => ({
                    ...s,
                    triggerFirstDeploy: e.target.checked,
                  }))
                }
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
      <span className="inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
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
      <Badge variant="outline" className="text-[10px]">
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
