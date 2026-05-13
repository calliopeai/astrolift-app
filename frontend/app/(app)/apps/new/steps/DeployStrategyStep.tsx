"use client";

import { ClockIcon, HandIcon, InfoIcon, ShieldCheckIcon, ZapIcon } from "lucide-react";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

import type { WizardState, WizardTriggerMode } from "../wizard-client";

interface Props {
  state: WizardState;
  setState: React.Dispatch<React.SetStateAction<WizardState>>;
  setValid: (valid: boolean) => void;
}

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
    comingSoon: true,
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
  // Validity:
  //   - auto_on_push: deploy_branch required
  //   - cron: cron expression must be 5-field
  //   - manual: always valid
  React.useEffect(() => {
    let ok = true;
    if (state.triggerMode === "auto_on_push") {
      ok = state.deployBranch.trim().length > 0;
    }
    if (state.triggerMode === "cron") {
      ok = CRON_FIELD.test(state.cronExpression.trim());
    }
    setValid(ok);
  }, [state.triggerMode, state.deployBranch, state.cronExpression, setValid]);

  return (
    <div className="flex flex-col gap-5">
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

      {state.triggerMode === "auto_on_push" && (
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

      {state.triggerMode === "cron" && (
        <section className="flex flex-col gap-3 rounded-md border border-amber-500/40 bg-amber-500/5 p-4">
          <div className="flex items-start gap-2">
            <InfoIcon className="mt-0.5 size-4 text-amber-600 dark:text-amber-400" />
            <p className="text-muted-foreground text-xs">
              The backend doesn&apos;t carry a schedule field yet — we&apos;ll capture the cron
              expression here, but the app will be registered with{" "}
              <code className="bg-muted rounded px-1 py-0.5 font-mono">manual</code> mode until
              scheduled triggers ship.
            </p>
          </div>
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
              }))
            }
          />
          <div className="flex-1">
            <span className="inline-flex items-center gap-2 font-medium">
              Require approval before deploying
              <Badge variant="secondary" className="gap-1 text-xs">
                <ShieldCheckIcon className="size-3" /> Safer
              </Badge>
              <Badge variant="outline" className="text-[10px]">
                Coming soon
              </Badge>
            </span>
            <p className="text-muted-foreground mt-1 text-xs">
              Triggered deploys sit in a &ldquo;pending approval&rdquo; state until an approver
              acks. The backend mutation for approvers isn&apos;t wired yet — your selection is
              captured for the follow-up.
            </p>
          </div>
        </label>

        {state.requiresApproval && (
          <div className="grid gap-3 pl-7 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="approver-team">Approver team (optional)</Label>
              <Input
                id="approver-team"
                value={state.approverTeamId}
                onChange={(e) => setState((s) => ({ ...s, approverTeamId: e.target.value }))}
                placeholder="team-id"
                className="font-mono text-xs"
                disabled
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="approver-users">Approver users (comma-separated)</Label>
              <Input
                id="approver-users"
                value={state.approverUserIds.join(", ")}
                onChange={(e) =>
                  setState((s) => ({
                    ...s,
                    approverUserIds: e.target.value
                      .split(",")
                      .map((v) => v.trim())
                      .filter(Boolean),
                  }))
                }
                placeholder="user-id-1, user-id-2"
                className="font-mono text-xs"
                disabled
              />
            </div>
          </div>
        )}
      </section>
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
