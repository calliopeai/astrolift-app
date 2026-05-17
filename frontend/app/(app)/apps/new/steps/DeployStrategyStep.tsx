"use client";

import { useQuery } from "@apollo/client/react";
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
import { LIST_PROJECTS } from "@/graphql/identity/identity.queries";
import type { AstroliftProject } from "@/graphql/identity/identity.types";
import { cn } from "@/lib/utils";

import { ApproverSelector } from "../components/ApproverSelector";
import type { DeployTiming, WizardState, WizardTriggerMode } from "../wizard-client";

interface ProjectsResponse {
  astroliftProjects: AstroliftProject[];
}

interface Props {
  state: WizardState;
  setState: React.Dispatch<React.SetStateAction<WizardState>>;
  setValid: (valid: boolean) => void;
}

const DEPLOY_TIMINGS: Array<{
  value: DeployTiming;
  label: string;
  description: string;
  icon: typeof RocketIcon;
}> = [
  {
    value: "now",
    label: "Deploy now",
    description: "Pick a trigger and kick off the first deploy as soon as registration completes.",
    icon: RocketIcon,
  },
  {
    value: "later",
    label: "Configure, deploy later",
    description: "Lock in trigger and approval settings now; first deploy runs on the next event.",
    icon: PauseIcon,
  },
  {
    value: "skip",
    label: "Skip — configure later",
    description:
      "Just register the app. Set up triggers, approvals, and deploys from the app page.",
    icon: CoffeeIcon,
  },
];

const TRIGGER_MODES: Array<{
  value: WizardTriggerMode;
  label: string;
  description: string;
  icon: typeof ZapIcon;
  comingSoon?: boolean;
}> = [
  {
    value: "auto_on_push",
    label: "Auto on push",
    description: "Deploy whenever a commit lands on the selected branch (via SCM webhook).",
    icon: ZapIcon,
  },
  {
    value: "cron",
    label: "Cron schedule",
    description: "Deploy on a recurring schedule.",
    icon: ClockIcon,
  },
  {
    value: "manual",
    label: "Manual only",
    description: "Deploys only run when someone explicitly triggers them.",
    icon: HandIcon,
  },
];

const CRON_FIELD = /^(\S+\s+){4}\S+$/;

function naturalCronHint(expr: string): string {
  // Lightweight read-back, not a real parser. Just translates a few
  // very common patterns so an operator can sanity-check before
  // submitting.
  const e = expr.trim();
  if (!CRON_FIELD.test(e)) return "Five-field cron expression expected.";
  if (e === "0 * * * *") return "Hourly, on the hour.";
  if (e === "*/5 * * * *") return "Every 5 minutes.";
  if (e === "0 3 * * *") return "Daily at 03:00.";
  if (e === "0 0 * * 0") return "Weekly on Sunday at 00:00.";
  if (e === "0 0 1 * *") return "Monthly on the 1st at 00:00.";
  return "Custom schedule.";
}

export function DeployStrategyStep({ state, setState, setValid }: Props) {
  const t = useTranslations("apps.wizard.approval");
  // Resolve the org slug from the picked project — the approver
  // picker is org-scoped so the user list mirrors the project's org.
  const projectsQuery = useQuery<ProjectsResponse>(LIST_PROJECTS, {
    fetchPolicy: "cache-first",
  });
  const orgSlug = React.useMemo(() => {
    const projects = projectsQuery.data?.astroliftProjects ?? [];
    const picked = projects.find((p) => p.id === state.projectId);
    return picked?.organization?.slug ?? "";
  }, [projectsQuery.data, state.projectId]);

  const [approverPickerValid, setApproverPickerValid] = React.useState(true);

  // Validity:
  //   - skip: always valid (operator finishes config from the app page)
  //   - auto_on_push: deploy_branch required
  //   - cron: cron expression must be 5-field
  //   - manual: always valid
  //   - approval gate (when on): approver picker reports its own validity
  React.useEffect(() => {
    if (state.deployTiming === "skip") {
      setValid(true);
      return;
    }
    let ok = true;
    if (state.triggerMode === "auto_on_push") {
      ok = state.deployBranch.trim().length > 0;
    }
    if (state.triggerMode === "cron") {
      ok = CRON_FIELD.test(state.cronExpression.trim());
    }
    if (state.requiresApproval && !approverPickerValid) {
      ok = false;
    }
    setValid(ok);
  }, [
    state.deployTiming,
    state.triggerMode,
    state.deployBranch,
    state.cronExpression,
    state.requiresApproval,
    approverPickerValid,
    setValid,
  ]);

  const showTriggerConfig = state.deployTiming !== "skip";

  return (
    <div className="flex flex-col gap-5">
      <section className="flex flex-col gap-3">
        <div>
          <h3 className="font-medium">When should this app deploy?</h3>
          <p className="text-muted-foreground text-xs">
            Pick a path for first deploy. You can change strategy later from the app page.
          </p>
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
              label={opt.label}
              description={opt.description}
            />
          ))}
        </div>
      </section>

      {showTriggerConfig && (
        <section className="flex flex-col gap-3">
          <div>
            <h3 className="font-medium">Trigger mode</h3>
            <p className="text-muted-foreground text-xs">What event causes a deploy to run?</p>
          </div>
          <div className="grid gap-2 md:grid-cols-3">
            {TRIGGER_MODES.map((opt) => (
              <OptionCard
                key={opt.value}
                selected={state.triggerMode === opt.value}
                onClick={() => setState((s) => ({ ...s, triggerMode: opt.value }))}
                icon={opt.icon}
                label={opt.label}
                description={opt.description}
                badge={opt.comingSoon ? "Coming soon" : undefined}
              />
            ))}
          </div>
        </section>
      )}

      {showTriggerConfig && state.triggerMode === "auto_on_push" && (
        <section className="flex flex-col gap-3 rounded-md border p-4">
          <div className="space-y-2">
            <Label htmlFor="deploy-branch">Deploy branch</Label>
            <Input
              id="deploy-branch"
              value={state.deployBranch}
              onChange={(e) => setState((s) => ({ ...s, deployBranch: e.target.value }))}
              placeholder={state.defaultBranch || "main"}
              className="font-mono text-xs"
            />
            <p className="text-muted-foreground text-xs">
              Pushes to this branch will trigger a deploy. Defaults to the repo&apos;s default
              branch.
            </p>
          </div>
        </section>
      )}

      {showTriggerConfig && state.triggerMode === "cron" && (
        <section className="flex flex-col gap-3 rounded-md border p-4">
          <div className="space-y-2">
            <Label htmlFor="cron-expression">Cron expression</Label>
            <Input
              id="cron-expression"
              value={state.cronExpression}
              onChange={(e) => setState((s) => ({ ...s, cronExpression: e.target.value }))}
              placeholder="0 3 * * *"
              className="font-mono text-xs"
            />
            <p className="text-muted-foreground text-xs">
              Five fields (minute hour day month weekday). {naturalCronHint(state.cronExpression)}
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

          {state.requiresApproval && (
            <ApproverSelector
              orgSlug={orgSlug}
              value={{
                approverUserIds: state.approverUserIds,
                approverTeamId: state.approverTeamId,
                minimumApprovals: state.minimumApprovals,
              }}
              onChange={(next) =>
                setState((s) => ({
                  ...s,
                  approverUserIds: next.approverUserIds,
                  approverTeamId: next.approverTeamId,
                  minimumApprovals: next.minimumApprovals,
                }))
              }
              onValidityChange={setApproverPickerValid}
            />
          )}
        </section>
      )}

      {!showTriggerConfig && (
        <section className="bg-muted/30 text-muted-foreground rounded-md border border-dashed p-4 text-xs">
          Trigger mode and approval gates are skipped for now. Once registration completes you can
          wire those up from the app&apos;s Settings tab.
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
          <Badge variant="outline" className="text-[10px]">
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
