"use client";

import { ArrowLeftIcon, ArrowRightIcon, CheckIcon, SparklesIcon, XIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

import { DeployStrategyStep, isStrategyValid } from "./DeployStrategyStep";
import { ProjectStep, isProjectValid } from "./ProjectStep";
import { RepoStep, type RepoStepData, isRepoValid } from "./RepoStep";
import type { OnboardingActions } from "./use-onboarding-actions";
import type { OnboardingStep, OnboardingWizardState } from "./types";
import { WelcomeStep, isWelcomeValid } from "./WelcomeStep";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The writes (team, project, app, complete or skip), from
   *  useOnboardingActions for the active organization. */
  actions: OnboardingActions;
  /** The form's state, owned by the caller so the repo step's data can
   *  follow the chosen connection. Reset it when the dialog opens. */
  state: OnboardingWizardState;
  setState: React.Dispatch<React.SetStateAction<OnboardingWizardState>>;
  /** The repo step's source connections and repos, from useRepoStepData. */
  repo: RepoStepData;
  /** Optional callback fired after the wizard closes either by
   *  completion or skip — handed back to the parent so the dashboard
   *  can kick off the spotlight tour. */
  onComplete?: () => void;
}

const TOTAL_STEPS: OnboardingStep[] = [1, 2, 3, 4];

/**
 * First-run onboarding wizard. Four steps, all dismissable:
 *  1. Welcome + team
 *  2. Project
 *  3. Repo picker (optional — skips if no source provider is wired)
 *  4. Deploy strategy + confirmation summary
 *
 * "Skip" on any step marks the org's onboarding as complete via
 * markOnboardingComplete(skip: true) so the wizard never re-prompts
 * on subsequent dashboard renders unless the operator explicitly
 * re-runs it from the user menu.
 *
 * Submit chains create-team -> create-project -> optionally
 * register-app -> markOnboardingComplete. Each step is awaited;
 * a failure surfaces a sonner toast and keeps the wizard open at
 * the failed step so the operator can fix and retry without losing
 * upstream state.
 */
export function OnboardingWizard({
  open,
  onOpenChange,
  state,
  setState,
  actions,
  repo,
  onComplete,
}: Props) {
  const t = useTranslations("onboarding");

  const stepValid = React.useMemo((): boolean => {
    switch (state.step) {
      case 1:
        return isWelcomeValid(state);
      case 2:
        return isProjectValid(state);
      case 3:
        return isRepoValid(state);
      case 4:
        return isStrategyValid(state);
    }
  }, [state]);

  async function handleSkip() {
    if (await actions.skip()) {
      onOpenChange(false);
      onComplete?.();
    }
  }

  async function handleSubmit() {
    setState((prev) => ({ ...prev, submitting: true, submitError: null }));
    const result = await actions.submit(state);
    if (!result.ok) {
      setState((prev) => ({
        ...prev,
        submitting: false,
        submitError: result.message,
        step: result.step ?? prev.step,
      }));
      return;
    }
    setState((prev) => ({ ...prev, submitting: false }));
    onOpenChange(false);
    onComplete?.();
  }

  function next() {
    if (state.step === 4) {
      void handleSubmit();
      return;
    }
    setState((prev) => ({ ...prev, step: (prev.step + 1) as OnboardingStep }));
  }

  function back() {
    if (state.step === 1) return;
    setState((prev) => ({ ...prev, step: (prev.step - 1) as OnboardingStep }));
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // Block accidental closes while a submit is in flight so the
        // operator doesn't lose context to a stray backdrop click;
        // the explicit Skip button stays available once it re-enables.
        if (state.submitting && !next) return;
        onOpenChange(next);
      }}
    >
      <DialogContent className="max-w-2xl" hideCloseButton>
        <DialogHeader>
          <div className="flex items-center gap-2">
            <SparklesIcon className="text-primary size-4" />
            <DialogTitle>{t("title")}</DialogTitle>
          </div>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>

        <ol className="flex items-center gap-2" aria-label={t("a11y.steps")}>
          {TOTAL_STEPS.map((idx) => {
            const isDone = idx < state.step;
            const isActive = idx === state.step;
            return (
              <li key={idx} className="flex flex-1 items-center gap-2">
                <span
                  className={cn(
                    "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-medium",
                    (isActive || isDone) && "border-primary bg-primary text-primary-foreground",
                    !isActive && !isDone && "border-muted-foreground/30 text-muted-foreground"
                  )}
                  aria-current={isActive ? "step" : undefined}
                >
                  {isDone ? <CheckIcon className="size-3.5" /> : idx}
                </span>
                {idx < TOTAL_STEPS.length && (
                  <div
                    className={cn("h-px flex-1", isDone ? "bg-primary" : "bg-muted-foreground/30")}
                  />
                )}
              </li>
            );
          })}
        </ol>

        <div className="min-h-[300px]">
          {state.step === 1 && <WelcomeStep state={state} setState={setState} />}
          {state.step === 2 && <ProjectStep state={state} setState={setState} />}
          {state.step === 3 && <RepoStep state={state} setState={setState} {...repo} />}
          {state.step === 4 && <DeployStrategyStep state={state} setState={setState} />}
        </div>

        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={handleSkip}
              disabled={state.submitting}
            >
              <XIcon className="size-4" />
              {t("skipCta")}
            </Button>
          </div>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={back}
              disabled={state.step === 1 || state.submitting}
            >
              <ArrowLeftIcon className="size-4" />
              {t("backCta")}
            </Button>
            <Button type="button" onClick={next} disabled={!stepValid || state.submitting}>
              {state.step === 4 ? t("finishCta") : t("nextCta")}
              {!state.submitting && <ArrowRightIcon className="size-4" />}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
