"use client";

import { RocketIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { type OnboardingWizardState, deriveSlug } from "./types";

interface Props {
  state: OnboardingWizardState;
  setState: React.Dispatch<React.SetStateAction<OnboardingWizardState>>;
}

/**
 * Step 1 — welcome copy + the first input the operator has to fill
 * in (team name). Slug is derived from the name unless the operator
 * has explicitly typed in the slug field (mirrors the apps-new
 * wizard's slug-touched pattern so a casual operator gets a sane
 * default without losing manual edits).
 */
export function WelcomeStep({ state, setState }: Props) {
  const t = useTranslations("onboarding.welcome");

  function handleNameChange(value: string) {
    setState((prev) => ({
      ...prev,
      teamName: value,
      teamSlug: prev.teamSlugTouched ? prev.teamSlug : deriveSlug(value),
    }));
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="bg-muted/40 flex items-start gap-3 rounded-lg border p-4">
        <div className="bg-primary/10 text-primary inline-flex size-9 items-center justify-center rounded-md">
          <RocketIcon className="size-4" />
        </div>
        <div>
          <p className="text-sm font-medium">{t("greeting")}</p>
          <p className="text-muted-foreground mt-1 text-sm">{t("description")}</p>
        </div>
      </div>

      <div className="grid gap-2">
        <Label htmlFor="ob-team-name">{t("teamNameLabel")}</Label>
        <Input
          id="ob-team-name"
          autoFocus
          maxLength={120}
          placeholder={t("teamNamePlaceholder")}
          value={state.teamName}
          onChange={(e) => handleNameChange(e.target.value)}
        />
      </div>

      <div className="grid gap-2">
        <Label htmlFor="ob-team-slug">{t("teamSlugLabel")}</Label>
        <Input
          id="ob-team-slug"
          maxLength={60}
          placeholder={t("teamSlugPlaceholder")}
          value={state.teamSlug}
          onChange={(e) =>
            setState((prev) => ({
              ...prev,
              teamSlug: deriveSlug(e.target.value),
              teamSlugTouched: true,
            }))
          }
        />
        <p className="text-muted-foreground text-xs">{t("teamSlugHelp")}</p>
      </div>
    </div>
  );
}

export function isWelcomeValid(state: OnboardingWizardState): boolean {
  return state.teamName.trim().length > 0 && /^[a-z0-9][a-z0-9-]{0,59}$/.test(state.teamSlug);
}
