"use client";

import { useMutation } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { REGISTER_APP } from "@/graphql/registry/registry.mutations";
import { LIST_APPS } from "@/graphql/registry/registry.queries";
import type {
  AstroliftRegisteredApp,
  SourceKind,
  TriggerMode,
} from "@/graphql/registry/registry.types";
import { PUSH_CI_WORKFLOW } from "@/graphql/scm/scm.mutations";
import type { AstroliftPushCiWorkflowResult, ScmConnectionKind } from "@/graphql/scm/scm.types";

import { isCiPushableKind } from "./ci-pushable";
import type { MutationResult } from "@/graphql/identity/identity.types";

import { WizardShell, type WizardStep } from "./components/WizardShell";
import { AppDetailsStep } from "./steps/AppDetailsStep";
import { DeployStrategyStep } from "./steps/DeployStrategyStep";
import { ManifestPreviewStep } from "./steps/ManifestPreviewStep";
import { RepoPickerStep } from "./steps/RepoPickerStep";
import { ReviewSubmitStep, type SideEffectStep } from "./steps/ReviewSubmitStep";

// Step shape — five steps plus a virtual "0" we never render.
type StepNumber = 1 | 2 | 3 | 4 | 5;

// Connection kinds that hold a usable write-token. OAuth-app config
// rows carry the app's client secret rather than a user token, so they
// can't drive a commit — pushCiWorkflow refuses them on the server
// side, and we suppress the UI affordance for them as well. The pushable
// set is shared via ./ci-pushable (#908).

function ciWorkflowPathForSourceKind(sourceKind: SourceKind): string {
  if (sourceKind === "gitlab") return ".gitlab-ci.yml";
  return ".github/workflows/astrolift-deploy.yml";
}

// Wizard-side trigger mode. Mirrors the backend's `RegisterAppInput`
// values — `cron` is persisted alongside a five-field expression
// (see #290). Kept as its own alias so step components can import
// the union without reaching for the full registry type module.
export type WizardTriggerMode = TriggerMode;

// Conversion-flow signal for *when* the operator wants to deploy
// after registration. "now" runs the trigger normally; "later"
// registers but keeps approval/trigger configuration in place;
// "skip" registers the app and hides downstream deploy config so a
// first-time operator can finish onboarding without picking a
// trigger mode. Pure FE concern — the backend mutation is unchanged;
// "skip" maps to `triggerMode: manual` + `triggerFirstDeploy: false`.
export type DeployTiming = "now" | "later" | "skip";

export interface WizardState {
  step: StepNumber;

  // Step 1
  connectionId: string;
  // The full connection kind discriminant — drives review-step
  // affordances like the CI-workflow push checkbox, which only
  // makes sense for github_* / gitlab_* (and OAuth-app config rows
  // don't have a usable token).
  connectionKind: ScmConnectionKind | "";
  sourceKind: SourceKind;
  sourceRepo: string;
  sourceUrl: string;
  defaultBranch: string;
  // Read off the picked repo's webhook-support flags for review-step UX.
  connectionIsAppInstall: boolean;

  // Step 2
  manifestPath: string;
  manifestRaw: string;
  manifestFromRepo: boolean; // false => either missing-on-repo or edited locally
  manifestValid: boolean;
  manifestErrors: string[];
  // Register without a manifest and add it later on the app's Manifest tab
  // (#1172). Lets an empty repo — or an agent config repo whose astrolift.toml
  // isn't an app manifest — finish onboarding instead of dead-ending on the
  // manifest gate. When set, the manifest step is always valid and submit
  // sends manifestRaw: null.
  manifestLater: boolean;

  // Step 3
  name: string;
  slug: string;
  slugTouched: boolean;
  description: string;
  projectId: string;

  // Step 4
  deployTiming: DeployTiming;
  triggerMode: WizardTriggerMode;
  deployBranch: string;
  cronExpression: string;
  requiresApproval: boolean;
  approverUserIds: string[];
  approverTeamId: string;
  // Required approver count (#410). Maps to the backend's
  // ``minimumApprovals``. Bounded by the user-picker size when the
  // policy gates on a user set; bounded at >= 1 always.
  minimumApprovals: number;

  // Step 5
  pushCiWorkflow: boolean;
  triggerFirstDeploy: boolean;
}

export function initialWizardState(): WizardState {
  return {
    step: 1,
    connectionId: "",
    connectionKind: "",
    sourceKind: "github",
    sourceRepo: "",
    sourceUrl: "",
    defaultBranch: "main",
    connectionIsAppInstall: false,
    manifestPath: "astrolift.toml",
    manifestRaw: "",
    manifestFromRepo: false,
    manifestValid: false,
    manifestErrors: [],
    manifestLater: false,
    name: "",
    slug: "",
    slugTouched: false,
    description: "",
    projectId: "",
    deployTiming: "now",
    triggerMode: "auto_on_push",
    deployBranch: "main",
    cronExpression: "0 * * * *",
    requiresApproval: false,
    approverUserIds: [],
    approverTeamId: "",
    minimumApprovals: 1,
    pushCiWorkflow: false,
    triggerFirstDeploy: true,
  };
}

const STEPS: WizardStep[] = [
  {
    key: "repo",
    label: "Repo",
    description: "Pick a source connection and a repository.",
  },
  {
    key: "manifest",
    label: "Manifest",
    description: "Review or draft the astrolift.toml manifest.",
  },
  {
    key: "details",
    label: "Details",
    description: "Name, slug, description, and where this app lives.",
  },
  {
    key: "strategy",
    label: "Strategy",
    description: "How and when deploys should run.",
  },
  {
    key: "review",
    label: "Review",
    description: "Final summary and the things we'll do on submit.",
  },
];

/**
 * Top-level state machine for the Register App wizard. Holds the
 * single `useState<WizardState>` mandated by the brief — every step
 * gets `state` and `setState` (or a focused setter) and decides for
 * itself when it's valid. The shell binds Back/Next based on per-step
 * validity, which each step reports through `setStepValid`.
 */
export function WizardClient() {
  const router = useRouter();
  const [state, setState] = React.useState<WizardState>(initialWizardState);
  const [stepValid, setStepValid] = React.useState<Record<StepNumber, boolean>>({
    1: false,
    2: false,
    3: false,
    4: false,
    5: true,
  });
  const [submitting, setSubmitting] = React.useState(false);
  const [submitError, setSubmitError] = React.useState<string | null>(null);
  const [sideEffects, setSideEffects] = React.useState<SideEffectStep[]>([]);

  const [registerApp] = useMutation<{
    registerApp: MutationResult<AstroliftRegisteredApp>;
  }>(REGISTER_APP, {
    refetchQueries: [{ query: LIST_APPS }],
    awaitRefetchQueries: true,
  });

  const [pushCiWorkflowMutation] = useMutation<{
    pushCiWorkflow: MutationResult<AstroliftPushCiWorkflowResult>;
  }>(PUSH_CI_WORKFLOW);

  // ---- Step movement ----

  const goTo = React.useCallback((next: StepNumber) => {
    setState((s) => ({ ...s, step: next }));
  }, []);

  const goBack = React.useCallback(() => {
    setState((s) => ({
      ...s,
      step: Math.max(1, s.step - 1) as StepNumber,
    }));
  }, []);

  const goNext = React.useCallback(() => {
    setState((s) => ({
      ...s,
      step: Math.min(5, s.step + 1) as StepNumber,
    }));
  }, []);

  const setValid = React.useCallback((step: StepNumber, valid: boolean) => {
    setStepValid((prev) => (prev[step] === valid ? prev : { ...prev, [step]: valid }));
  }, []);

  // ---- Cancel ----

  const [confirmCancel, setConfirmCancel] = React.useState(false);

  const handleCancel = React.useCallback(() => {
    const dirty =
      state.connectionId !== "" ||
      state.sourceRepo !== "" ||
      state.name !== "" ||
      state.manifestRaw !== "";
    if (dirty) {
      setConfirmCancel(true);
      return;
    }
    router.push("/apps");
  }, [router, state]);

  // ---- Submit ----

  async function submit() {
    if (!state.projectId) {
      toast.error("Pick a project before submitting.");
      return;
    }
    setSubmitting(true);
    setSubmitError(null);

    // The CI-workflow path is only valid for connection kinds that
    // carry a write-token. Anything else (OAuth-app config rows,
    // missing connection) gets the "skipped" badge with a friendly
    // reason rather than a failed dispatch.
    const ciWorkflowPath = ciWorkflowPathForSourceKind(state.sourceKind);
    const ciWorkflowEnabled = state.pushCiWorkflow && isCiPushableKind(state.connectionKind);
    const ciWorkflowSkippedNote =
      state.pushCiWorkflow && !isCiPushableKind(state.connectionKind)
        ? "Connection kind can't push commits — toggle ignored."
        : !state.pushCiWorkflow
          ? "Operator opted out."
          : "";

    const plan: SideEffectStep[] = [
      { key: "register", label: "Register app", status: "pending" },
      {
        key: "ci_workflow",
        label: ciWorkflowPath,
        status: ciWorkflowEnabled ? "pending" : "skipped",
        note: ciWorkflowEnabled ? undefined : ciWorkflowSkippedNote,
      },
      ...(state.triggerFirstDeploy
        ? [
            {
              key: "first_deploy",
              label: "Trigger first deploy",
              status: "skipped" as const,
              note: "Will run once the onboarding workflow lands the app in `ready`",
            },
          ]
        : []),
    ];
    setSideEffects(plan);

    const update = (key: string, patch: Partial<SideEffectStep>) =>
      setSideEffects((prev) => prev.map((s) => (s.key === key ? { ...s, ...patch } : s)));

    try {
      update("register", { status: "running" });
      // Approval policy (#410) is only sent when the deploy-strategy
      // step is active *and* the operator turned the gate on. "skip"
      // timing intentionally drops the policy so a first-time operator
      // can finish registration without committing to one — the
      // app-detail Settings tab is the canonical place to wire it up
      // post-registration.
      const approvalActive = state.deployTiming !== "skip" && state.requiresApproval;
      const requiresApproval = approvalActive;
      const approverUserIds = approvalActive ? state.approverUserIds : null;
      const approverTeamId =
        approvalActive && state.approverTeamId !== "" ? state.approverTeamId : null;
      const minimumApprovals = approvalActive ? Math.max(1, state.minimumApprovals) : null;

      const { data } = await registerApp({
        variables: {
          input: {
            projectId: state.projectId,
            name: state.name.trim(),
            slug: state.slug.trim(),
            description: state.description.trim() || null,
            sourceKind: state.sourceKind,
            sourceRepo: state.sourceRepo.trim(),
            sourceUrl: state.sourceUrl.trim() || null,
            manifestPath: state.manifestPath.trim() || "astrolift.toml",
            // "Set up manifest later" registers the app with no manifest —
            // send an explicit null rather than relying on the empty-string
            // coalesce (#1172). The app page surfaces the missing manifest and
            // the Manifest tab is where the operator adds it.
            manifestRaw: state.manifestLater ? null : state.manifestRaw.trim() || null,
            defaultBranch: state.defaultBranch.trim() || "main",
            deployBranch: state.deployBranch.trim() || "main",
            // "skip" timing forces manual trigger so the app registers
            // cleanly without committing to a deploy strategy yet —
            // operator can configure it from the app detail page.
            triggerMode: state.deployTiming === "skip" ? "manual" : state.triggerMode,
            cronExpression:
              state.deployTiming !== "skip" && state.triggerMode === "cron"
                ? state.cronExpression.trim()
                : null,
            requiresApproval,
            approverUserIds,
            approverTeamId,
            minimumApprovals,
          },
        },
      });
      const result = data?.registerApp;
      if (!result?.ok || !result.data) {
        const msg = result?.errors?.[0]?.message ?? "Register failed";
        update("register", { status: "failed", error: msg });
        setSubmitError(msg);
        setSubmitting(false);
        return;
      }
      update("register", { status: "done" });
      toast.success(`Registered ${result.data.slug}`);

      // The CI-workflow push is best-effort: register has already
      // succeeded, so we surface failure on the side-effect row
      // rather than rolling the app back. The operator can re-run
      // the push from the app detail page once they fix whatever
      // caused the rejection (token rotated, branch protected, etc.).
      if (ciWorkflowEnabled) {
        update("ci_workflow", { status: "running" });
        try {
          const { data: pushData } = await pushCiWorkflowMutation({
            variables: {
              input: {
                appId: result.data.id,
                connectionId: state.connectionId,
              },
            },
          });
          const pushResult = pushData?.pushCiWorkflow;
          if (!pushResult?.ok || !pushResult.data) {
            const msg = pushResult?.errors?.[0]?.message ?? "pushCiWorkflow failed";
            update("ci_workflow", { status: "failed", error: msg });
          } else {
            update("ci_workflow", {
              status: "done",
              note: pushResult.data.repoUrl
                ? `Committed ${pushResult.data.commitSha.slice(0, 7)} — ${pushResult.data.repoUrl}`
                : `Committed ${pushResult.data.commitSha.slice(0, 7)}`,
            });
          }
        } catch (err) {
          const msg = err instanceof Error ? err.message : "pushCiWorkflow failed";
          update("ci_workflow", { status: "failed", error: msg });
        }
      }

      // Land the operator where the next action is:
      //   - manifest-later (#1172): the Manifest tab, to add/sync the
      //     astrolift.toml they deferred — takes precedence since a missing
      //     manifest is the more fundamental gap;
      //   - deploy "skip" (#901): Settings, to finish deploy config, instead
      //     of an Overview that will never show a deploy;
      //   - otherwise: the app Overview.
      router.push(
        state.manifestLater
          ? `/apps/${result.data.slug}/manifest`
          : state.deployTiming === "skip"
            ? `/apps/${result.data.slug}/settings`
            : `/apps/${result.data.slug}`
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Register failed";
      update("register", { status: "failed", error: msg });
      setSubmitError(msg);
      setSubmitting(false);
    }
  }

  // ---- Step body + footer-binding ----

  const isLast = state.step === 5;
  const canGoNext = stepValid[state.step] ?? false;

  return (
    <WizardShell
      step={state.step}
      steps={STEPS}
      onStepClick={(idx) => goTo(idx as StepNumber)}
      onBack={state.step > 1 && !submitting ? goBack : undefined}
      onNext={isLast ? submit : canGoNext ? goNext : undefined}
      onCancel={!submitting ? handleCancel : undefined}
      nextLabel={isLast ? (submitting ? "Submitting…" : "Submit") : "Next"}
      nextDisabled={!canGoNext}
      nextLoading={submitting}
    >
      {state.step === 1 && (
        <RepoPickerStep state={state} setState={setState} setValid={(v) => setValid(1, v)} />
      )}
      {state.step === 2 && (
        <ManifestPreviewStep state={state} setState={setState} setValid={(v) => setValid(2, v)} />
      )}
      {state.step === 3 && (
        <AppDetailsStep state={state} setState={setState} setValid={(v) => setValid(3, v)} />
      )}
      {state.step === 4 && (
        <DeployStrategyStep state={state} setState={setState} setValid={(v) => setValid(4, v)} />
      )}
      {state.step === 5 && (
        <ReviewSubmitStep
          state={state}
          setState={setState}
          steps={STEPS}
          onJumpToStep={(idx) => goTo(idx as StepNumber)}
          submitting={submitting}
          submitError={submitError}
          sideEffects={sideEffects}
        />
      )}
      <ConfirmDialog
        open={confirmCancel}
        onOpenChange={setConfirmCancel}
        title="Discard wizard progress?"
        description="The repo selection, app details, and manifest preview you've built so far are not saved anywhere. Closing the wizard now means starting over."
        confirmLabel="Discard and exit"
        destructive
        onConfirm={() => {
          router.push("/apps");
        }}
      />
    </WizardShell>
  );
}

export default WizardClient;
