"use client";

import {
  ClockIcon,
  CoffeeIcon,
  HandIcon,
  PauseIcon,
  RocketIcon,
  ShieldCheckIcon,
  ZapIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { TriggerMode } from "@/graphql/registry/registry.types";
import { cn } from "@/lib/utils";

import { CRON_FIELD } from "./use-app-deploy-strategy-step";

/** When the operator wants to deploy after registration (wizard `DeployTiming`). */
export type AppDeployTiming = "now" | "later" | "skip";

/** The wizard-state fields this step reads and writes. */
export interface AppDeployStrategyFields {
  deployTiming: AppDeployTiming;
  triggerMode: TriggerMode;
  deployBranch: string;
  defaultBranch: string;
  cronExpression: string;
  requiresApproval: boolean;
  approverUserIds: string[];
  approverTeamId: string;
  minimumApprovals: number;
  triggerFirstDeploy: boolean;
}

export interface AppDeployStrategyStepProps<S extends AppDeployStrategyFields> {
  state: S;
  setState: React.Dispatch<React.SetStateAction<S>>;
  /** The approver picker, rendered while the approval gate is on. */
  approverSlot: React.ReactNode;
}

/** Labels live under `appWizard.strategy.timing.<value>`. */
const DEPLOY_TIMINGS: Array<{
  value: AppDeployTiming;
  icon: typeof RocketIcon;
}> = [
  { value: "now", icon: RocketIcon },
  { value: "later", icon: PauseIcon },
  { value: "skip", icon: CoffeeIcon },
];

/** Labels live under `apps.deployStrategy.modes.<value>`, descriptions under `appWizard.strategy.modes.<value>`. */
const TRIGGER_MODES: Array<{
  value: TriggerMode;
  icon: typeof ZapIcon;
  comingSoon?: boolean;
}> = [
  { value: "auto_on_push", icon: ZapIcon },
  { value: "cron", icon: ClockIcon },
  { value: "manual", icon: HandIcon },
];

/** Key under `appWizard.strategy.cronHint`. */
function naturalCronHint(expr: string): string {
  // Lightweight read-back, not a real parser. Just translates a few
  // very common patterns so an operator can sanity-check before
  // submitting.
  const e = expr.trim();
  if (!CRON_FIELD.test(e)) return "invalid";
  if (e === "0 * * * *") return "hourly";
  if (e === "*/5 * * * *") return "every5";
  if (e === "0 3 * * *") return "daily";
  if (e === "0 0 * * 0") return "weekly";
  if (e === "0 0 1 * *") return "monthly";
  return "custom";
}

/**
 * Wizard step 4: deploy timing, trigger mode, and the approval gate.
 */
export function AppDeployStrategyStep<S extends AppDeployStrategyFields>({
  state,
  setState,
  approverSlot,
}: AppDeployStrategyStepProps<S>) {
  const t = useTranslations("apps.wizard.approval");
  const copy = useTranslations("appWizard.strategy");
  const modes = useTranslations("apps.deployStrategy.modes");

  const showTriggerConfig = state.deployTiming !== "skip";

  return (
    <div className="flex flex-col gap-5">
      <section className="flex flex-col gap-3">
        <div>
          <h3 className="font-medium">{copy("timingTitle")}</h3>
          <p className="text-muted-foreground text-xs">{copy("timingHelp")}</p>
        </div>
        <div className="grid gap-2 md:grid-cols-3">
          {DEPLOY_TIMINGS.map((opt) => (
            <OptionCard
              key={opt.value}
              selected={state.deployTiming === opt.value}
              onClick={() =>
                setState((s) => ({
                  ...s,
                  deployTiming: opt.value,
                  // Mirror the side-effect toggle so review-step status matches.
                  triggerFirstDeploy: opt.value === "now",
                }))
              }
              icon={opt.icon}
              label={copy(`timing.${opt.value}.label`)}
              description={copy(`timing.${opt.value}.description`)}
            />
          ))}
        </div>
      </section>

      {showTriggerConfig && (
        <section className="flex flex-col gap-3">
          <div>
            <h3 className="font-medium">{copy("triggerTitle")}</h3>
            <p className="text-muted-foreground text-xs">{copy("triggerHelp")}</p>
          </div>
          <div className="grid gap-2 md:grid-cols-3">
            {TRIGGER_MODES.map((opt) => (
              <OptionCard
                key={opt.value}
                selected={state.triggerMode === opt.value}
                onClick={() => setState((s) => ({ ...s, triggerMode: opt.value }))}
                icon={opt.icon}
                label={modes(`${opt.value}.label`)}
                description={copy(`modes.${opt.value}`)}
                badge={opt.comingSoon ? copy("comingSoon") : undefined}
              />
            ))}
          </div>
        </section>
      )}

      {showTriggerConfig && state.triggerMode === "auto_on_push" && (
        <section className="flex flex-col gap-3 rounded-md border p-4">
          <div className="space-y-2">
            <Label htmlFor="deploy-branch">{copy("deployBranch")}</Label>
            <Input
              id="deploy-branch"
              value={state.deployBranch}
              onChange={(e) => setState((s) => ({ ...s, deployBranch: e.target.value }))}
              placeholder={state.defaultBranch || "main"}
              className="font-mono text-xs"
              aria-invalid={!state.deployBranch.trim() || undefined}
            />
            {state.deployBranch.trim() ? (
              <p className="text-muted-foreground text-xs">{copy("deployBranchHelp")}</p>
            ) : (
              <p role="alert" className="text-destructive text-xs">
                {copy("deployBranchRequired")}
              </p>
            )}
          </div>
        </section>
      )}

      {showTriggerConfig && state.triggerMode === "cron" && (
        <section className="flex flex-col gap-3 rounded-md border p-4">
          <div className="space-y-2">
            <Label htmlFor="cron-expression">{copy("cronLabel")}</Label>
            <Input
              id="cron-expression"
              value={state.cronExpression}
              onChange={(e) => setState((s) => ({ ...s, cronExpression: e.target.value }))}
              placeholder="0 3 * * *"
              className="font-mono text-xs"
              aria-invalid={!CRON_FIELD.test(state.cronExpression.trim()) || undefined}
            />
            <p
              role={CRON_FIELD.test(state.cronExpression.trim()) ? undefined : "alert"}
              className={cn(
                "text-xs",
                CRON_FIELD.test(state.cronExpression.trim())
                  ? "text-muted-foreground"
                  : "text-destructive"
              )}
            >
              {copy("cronHelp")} {copy(`cronHint.${naturalCronHint(state.cronExpression)}`)}
            </p>
          </div>
        </section>
      )}

      {showTriggerConfig && (
        <section className="flex flex-col gap-3 rounded-md border p-4">
          <label className="flex items-start gap-3">
            <input
              type="checkbox"
              className="mt-1"
              checked={state.requiresApproval}
              onChange={(e) =>
                setState((s) => ({
                  ...s,
                  requiresApproval: e.target.checked,
                  approverUserIds: e.target.checked ? s.approverUserIds : [],
                  approverTeamId: e.target.checked ? s.approverTeamId : "",
                  minimumApprovals: e.target.checked ? Math.max(1, s.minimumApprovals) : 1,
                }))
              }
            />
            <div className="flex-1">
              <span className="inline-flex items-center gap-2 font-medium">
                {t("toggleLabel")}
                <Badge variant="secondary" className="gap-1 text-xs">
                  <ShieldCheckIcon className="size-3" /> {t("safer")}
                </Badge>
              </span>
              <p className="text-muted-foreground mt-1 text-xs">{t("toggleHint")}</p>
            </div>
          </label>

          {state.requiresApproval && approverSlot}
        </section>
      )}

      {!showTriggerConfig && (
        <section className="bg-muted/30 text-muted-foreground rounded-md border border-dashed p-4 text-xs">
          {copy("skipped")}
        </section>
      )}
    </div>
  );
}

function OptionCard({
  selected,
  onClick,
  icon: Icon,
  label,
  description,
  badge,
}: {
  selected: boolean;
  onClick: () => void;
  icon: typeof ZapIcon;
  label: string;
  description: string;
  badge?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex flex-col items-start gap-2 rounded-md border p-4 text-left transition-colors",
        selected
          ? "border-primary bg-primary/5 ring-primary/20 ring-2"
          : "hover:bg-muted/50 border-border"
      )}
    >
      <div className="flex w-full items-center justify-between">
        <Icon className={cn("size-5", selected ? "text-primary" : "text-muted-foreground")} />
        {badge && (
          <Badge variant="outline" className="text-2xs">
            {badge}
          </Badge>
        )}
      </div>
      <div>
        <p className="font-medium">{label}</p>
        <p className="text-muted-foreground mt-1 text-xs leading-relaxed">{description}</p>
      </div>
    </button>
  );
}
