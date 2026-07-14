"use client";

import { GitBranchIcon, LayersIcon, RocketIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { DefinitionList } from "@/components/ui/definition-list";
import { cn } from "@/lib/utils";

import type { DeployStrategy, OnboardingWizardState } from "./types";

interface Props {
  state: OnboardingWizardState;
  setState: React.Dispatch<React.SetStateAction<OnboardingWizardState>>;
}

const STRATEGIES: Array<{
  value: DeployStrategy;
  icon: typeof RocketIcon;
}> = [
  { value: "rolling", icon: LayersIcon },
  { value: "blue-green", icon: GitBranchIcon },
  { value: "canary", icon: RocketIcon },
];

/**
 * Step 4 — pick a deploy strategy + render the confirmation summary
 * the operator will sign off on with the final Submit. Strategy is
 * captured as intent only — astrolift.toml is the canonical source
 * for deploy config, and the user can tune it on the app config page
 * after onboarding.
 */
export function DeployStrategyStep({ state, setState }: Props) {
  const t = useTranslations("onboarding.strategy");

  return (
    <div className="flex flex-col gap-5">
      <div>
        <p className="text-sm font-medium">{t("title")}</p>
        <p className="text-muted-foreground mt-1 text-sm">{t("description")}</p>
      </div>

      <fieldset className="grid gap-2 sm:grid-cols-3" aria-label={t("title")} role="radiogroup">
        {STRATEGIES.map(({ value, icon: Icon }) => {
          const checked = state.deployStrategy === value;
          return (
            <label
              key={value}
              className={cn(
                "hover:bg-accent/40 flex cursor-pointer flex-col gap-1 rounded-md border p-3 text-sm transition-colors",
                checked && "border-primary bg-primary/5"
              )}
            >
              <input
                type="radio"
                name="ob-deploy-strategy"
                value={value}
                className="sr-only"
                checked={checked}
                onChange={() => setState((prev) => ({ ...prev, deployStrategy: value }))}
              />
              <Icon className={cn("size-4", checked ? "text-primary" : "text-muted-foreground")} />
              <span className="font-medium">{t(`options.${value}.label`)}</span>
              <span className="text-muted-foreground text-xs">
                {t(`options.${value}.description`)}
              </span>
            </label>
          );
        })}
      </fieldset>

      <div className="grid gap-2 text-sm">
        <p className="text-xs font-semibold tracking-wide uppercase">{t("summaryTitle")}</p>
        <DefinitionList
          items={[
            { term: t("summary.team"), description: `${state.teamName} (${state.teamSlug})` },
            {
              term: t("summary.project"),
              description: `${state.projectName} (${state.projectSlug})`,
            },
            {
              term: t("summary.repo"),
              description: state.sourceRepo || t("summary.repoSkipped"),
            },
            {
              term: t("summary.strategy"),
              description: t(`options.${state.deployStrategy}.label`),
            },
          ]}
        />
      </div>

      <p className="text-muted-foreground text-xs">{t("intentNote")}</p>
    </div>
  );
}

export function isStrategyValid(state: OnboardingWizardState): boolean {
  return (
    state.deployStrategy === "rolling" ||
    state.deployStrategy === "blue-green" ||
    state.deployStrategy === "canary"
  );
}
