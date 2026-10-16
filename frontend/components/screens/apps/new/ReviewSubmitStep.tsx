"use client";

import { AlertCircleIcon, Loader2Icon } from "lucide-react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import type { SourceKind, TriggerMode } from "@/graphql/registry/registry.types";
import { detectBuildPaths } from "@/lib/manifest/build-paths";

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
  /** Null: the detected value (see `detectBuildPaths`). */
  buildContext: string | null;
  dockerfilePath: string | null;
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

/** Trigger modes with a label under `apps.deployStrategy.modes`. */
const TRIGGER_MODES = new Set(["auto_on_push", "cron", "manual", "external_ci"]);

/** Deploy timings with a label under `appWizard.review.timing`. */
const DEPLOY_TIMINGS = new Set(["now", "later", "skip"]);

type ReviewT = ReturnType<typeof useTranslations<"appWizard.review">>;

function describeApprovalPolicy(state: ReviewSubmitFields, t: ReviewT): string {
  if (!state.requiresApproval) return t("approval.notRequired");
  if (state.approverTeamId) {
    return t("approval.team", { count: state.minimumApprovals });
  }
  if (state.approverUserIds.length > 0) {
    return t("approval.users", {
      min: state.minimumApprovals,
      count: state.approverUserIds.length,
    });
  }
  return t("approval.none");
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
  const t = useTranslations("appWizard.review");
  const modes = useTranslations("apps.deployStrategy.modes");
  const timing = (value: string) => (DEPLOY_TIMINGS.has(value) ? t(`timing.${value}`) : value);
  const trigger = (mode: string) => (TRIGGER_MODES.has(mode) ? modes(`${mode}.label`) : mode);
  const code = (chunks: ReactNode) => (
    <code className="bg-muted rounded px-1 py-0.5 font-mono">{chunks}</code>
  );
  // A manifest that sets its own build paths decides them, unless typed over.
  const detected = detectBuildPaths(state.manifestPath, state.manifestRaw);
  const fromManifest = detected.source === "manifestPaths";
  const buildRow = (label: string, typed: string | null, value: string) => {
    const own = typed?.trim();
    return own || !fromManifest
      ? { label, value: own || value, mono: true }
      : { label, value: t("buildFromManifest") };
  };
  return (
    <div className="flex flex-col gap-5">
      <section className="grid min-w-0 gap-3 sm:grid-cols-2">
        <SummaryCard
          step={1}
          title={t("source")}
          onJump={onJumpToStep}
          rows={[
            { label: t("repository"), value: state.sourceRepo, mono: true },
            { label: t("defaultBranch"), value: state.defaultBranch, mono: true },
            { label: t("sourceKind"), value: state.sourceKind },
            { label: t("manifestPath"), value: state.manifestPath, mono: true },
            buildRow(t("buildContext"), state.buildContext, detected.buildContext),
            buildRow(t("dockerfile"), state.dockerfilePath, detected.dockerfilePath),
            {
              label: t("manifest"),
              value: state.manifestLater
                ? t("manifestLater")
                : t("manifestState", {
                    origin: state.manifestFromRepo ? "repo" : "draft",
                    valid: state.manifestValid ? "yes" : "no",
                  }),
            },
          ]}
        />
        <SummaryCard
          step={2}
          title={t("run")}
          onJump={onJumpToStep}
          rows={[
            { label: t("name"), value: state.name },
            { label: t("slug"), value: state.slug, mono: true },
            ...(state.description ? [{ label: t("description"), value: state.description }] : []),
            ...(state.deployTiming === "skip"
              ? [
                  { label: t("deployTiming"), value: timing(state.deployTiming) },
                  { label: t("trigger"), value: t("triggerLater") },
                ]
              : [
                  {
                    label: t("deployTiming"),
                    value: timing(state.deployTiming),
                  },
                  {
                    label: t("trigger"),
                    value: trigger(state.triggerMode),
                  },
                  { label: t("deployBranch"), value: state.deployBranch, mono: true },
                  ...(state.triggerMode === "cron"
                    ? [{ label: t("cron"), value: state.cronExpression, mono: true }]
                    : []),
                  { label: t("approvalGate"), value: describeApprovalPolicy(state, t) },
                ]),
          ]}
        />
      </section>

      <WhatWillDeploy state={state} />

      <section className="flex flex-col gap-3">
        <div>
          <h3 className="font-medium">{t("sideEffectsTitle")}</h3>
          <p className="text-muted-foreground text-xs">{t("sideEffectsHelp")}</p>
        </div>

        <ul className="flex flex-col divide-y rounded-md border">
          <li className="flex items-center gap-3 p-3">
            <CheckboxLocked checked disabled />
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="text-sm">{t("register")}</span>
              <span className="text-muted-foreground text-xs">
                {t.rich("registerHelp", { code })}
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
              <span className="text-sm">{t("webhook")}</span>
              <span className="text-muted-foreground text-xs">
                {state.connectionIsAppInstall ? t("webhookCovered") : t("webhookPending")}
              </span>
            </div>
            <Badge variant="outline" className="text-2xs">
              {state.connectionIsAppInstall ? t("alreadyInstalled") : t("comingSoon")}
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
                {t.rich("pushWorkflow", { path: ciWorkflowPathFor(state.sourceKind), code })}
              </span>
              <span className="text-muted-foreground text-xs">
                {canPushCiWorkflow ? t("pushWorkflowHelp") : t("pushWorkflowUnavailable")}
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
              <span className="text-sm">{t("firstDeploy")}</span>
              <span className="text-muted-foreground text-xs">
                {t.rich("firstDeployHelp", { code })}
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
            {t("submitFailed")}
          </p>
          <p className="text-destructive/80 mt-1 text-xs">{submitError}</p>
        </div>
      )}

      {submitting && sideEffects.length === 0 && (
        <p className="text-muted-foreground inline-flex items-center gap-2 text-xs">
          <Loader2Icon className="size-3.5 animate-spin" />
          {t("submitting")}
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
  const t = useTranslations("appWizard.review");
  const shapes = useTranslations("vizRender");
  const preview = state.manifestLater ? null : deployPreview(state.manifestRaw);
  const deploys = state.deployTiming !== "skip" && state.triggerFirstDeploy;
  return (
    <section className="flex min-w-0 flex-col gap-3">
      <div>
        <h3 className="font-medium">{t("willDeployTitle")}</h3>
        <p className="text-muted-foreground text-xs">
          {deploys ? t("willDeployFirst") : t("willDeployNothing")}
        </p>
      </div>
      {state.manifestLater ? (
        <p className="text-muted-foreground rounded-md border border-dashed p-3 text-xs">
          {t("noManifest")}
        </p>
      ) : !preview || preview.workloads.length === 0 ? (
        <p className="text-muted-foreground rounded-md border border-dashed p-3 text-xs">
          {t("noWorkloads")}
        </p>
      ) : (
        <div className="min-w-0 rounded-md border">
          <div className="flex min-w-0 flex-wrap items-center gap-2 border-b p-3 text-sm">
            <span className="font-medium">{shapes(`topology.${preview.topology}`)}</span>
            <span className="text-muted-foreground text-xs">
              {shapes(`topologyBlurbs.${preview.topology}`)}
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
                  {t("managedService")}
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
