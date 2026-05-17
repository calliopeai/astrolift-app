"use client";

import { FileBoxIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

import { type OnboardingWizardState, deriveSlug } from "./types";

interface Props {
  state: OnboardingWizardState;
  setState: React.Dispatch<React.SetStateAction<OnboardingWizardState>>;
}

/**
 * Step 2 — project name / slug / optional description, scoped to the
 * team created in step 1. Same slug-touched-vs-derived pattern as the
 * welcome step.
 */
export function ProjectStep({ state, setState }: Props) {
  const t = useTranslations("onboarding.project");

  function handleNameChange(value: string) {
    setState((prev) => ({
      ...prev,
      projectName: value,
      projectSlug: prev.projectSlugTouched ? prev.projectSlug : deriveSlug(value),
    }));
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="bg-muted/40 flex items-start gap-3 rounded-lg border p-4">
        <div className="bg-primary/10 text-primary inline-flex size-9 items-center justify-center rounded-md">
          <FileBoxIcon className="size-4" />
        </div>
        <div>
          <p className="text-sm font-medium">{t("greeting", { team: state.teamName })}</p>
          <p className="text-muted-foreground mt-1 text-sm">{t("description")}</p>
        </div>
      </div>

      <div className="grid gap-2">
        <Label htmlFor="ob-project-name">{t("projectNameLabel")}</Label>
        <Input
          id="ob-project-name"
          autoFocus
          maxLength={120}
          placeholder={t("projectNamePlaceholder")}
          value={state.projectName}
          onChange={(e) => handleNameChange(e.target.value)}
        />
      </div>

      <div className="grid gap-2">
        <Label htmlFor="ob-project-slug">{t("projectSlugLabel")}</Label>
        <Input
          id="ob-project-slug"
          maxLength={60}
          placeholder={t("projectSlugPlaceholder")}
          value={state.projectSlug}
          onChange={(e) =>
            setState((prev) => ({
              ...prev,
              projectSlug: deriveSlug(e.target.value),
              projectSlugTouched: true,
            }))
          }
        />
        <p className="text-muted-foreground text-xs">
          {state.teamSlug || "team"}/{state.projectSlug || "project"}
        </p>
      </div>

      <div className="grid gap-2">
        <Label htmlFor="ob-project-description">{t("projectDescriptionLabel")}</Label>
        <Textarea
          id="ob-project-description"
          rows={3}
          maxLength={500}
          placeholder={t("projectDescriptionPlaceholder")}
          value={state.projectDescription}
          onChange={(e) => setState((prev) => ({ ...prev, projectDescription: e.target.value }))}
        />
      </div>
    </div>
  );
}

export function isProjectValid(state: OnboardingWizardState): boolean {
  return state.projectName.trim().length > 0 && /^[a-z0-9][a-z0-9-]{0,59}$/.test(state.projectSlug);
}
