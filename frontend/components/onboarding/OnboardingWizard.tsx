"use client";

import { useMutation } from "@apollo/client/react";
import { ArrowLeftIcon, ArrowRightIcon, CheckIcon, SparklesIcon, XIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { GET_ONBOARDING_STATE } from "@/graphql/identity/identity.queries";
import {
  CREATE_PROJECT,
  CREATE_TEAM,
  MARK_ONBOARDING_COMPLETE,
} from "@/graphql/identity/identity.mutations";
import type {
  AstroliftProject,
  AstroliftTeam,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { REGISTER_APP } from "@/graphql/registry/registry.mutations";
import { cn } from "@/lib/utils";

import { DeployStrategyStep, isStrategyValid } from "./DeployStrategyStep";
import { ProjectStep, isProjectValid } from "./ProjectStep";
import { RepoStep, isRepoValid } from "./RepoStep";
import { DEFAULT_ONBOARDING_STATE, type OnboardingStep, type OnboardingWizardState } from "./types";
import { WelcomeStep, isWelcomeValid } from "./WelcomeStep";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** GUID of the active organization. Required — the wizard creates
   *  a team scoped to this org. */
  organizationId: string;
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
export function OnboardingWizard({ open, onOpenChange, organizationId, onComplete }: Props) {
  const t = useTranslations("onboarding");
  const [state, setState] = React.useState<OnboardingWizardState>(DEFAULT_ONBOARDING_STATE);

  // Reset to defaults each time the dialog opens so a Cancel-then-
  // Reopen cycle doesn't leak stale form state from the prior run.
  React.useEffect(() => {
    if (open) setState(DEFAULT_ONBOARDING_STATE);
  }, [open]);

  const [markComplete] = useMutation<{
    markOnboardingComplete: MutationResult<{
      alreadyCompleted: boolean;
      organization: { id: string; onboardingCompletedAt: string | null };
    }>;
  }>(MARK_ONBOARDING_COMPLETE, { refetchQueries: [{ query: GET_ONBOARDING_STATE }] });

  const [createTeam] = useMutation<{ createTeam: MutationResult<AstroliftTeam> }>(CREATE_TEAM, {
    refetchQueries: [{ query: GET_ONBOARDING_STATE }],
  });
  const [createProject] = useMutation<{ createProject: MutationResult<AstroliftProject> }>(
    CREATE_PROJECT
  );
  const [registerApp] = useMutation(REGISTER_APP);

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
    try {
      const { data } = await markComplete({ variables: { input: { skip: true } } });
      const ok = data?.markOnboardingComplete.ok ?? false;
      if (!ok) {
        toast.error(t("toast.skipFailed"));
        return;
      }
      onOpenChange(false);
      onComplete?.();
    } catch (err) {
      const message = err instanceof Error ? err.message : t("toast.skipFailed");
      toast.error(message);
    }
  }

  async function handleSubmit() {
    setState((prev) => ({ ...prev, submitting: true, submitError: null }));
    try {
      // 1. Team
      const teamResp = await createTeam({
        variables: {
          input: {
            organizationId,
            name: state.teamName.trim(),
            slug: state.teamSlug,
          },
        },
      });
      const teamResult = teamResp.data?.createTeam;
      if (!teamResult?.ok || !teamResult.data) {
        const msg = teamResult?.errors?.[0]?.message ?? t("toast.teamFailed");
        setState((prev) => ({ ...prev, submitting: false, submitError: msg, step: 1 }));
        toast.error(msg);
        return;
      }
      const team = teamResult.data;

      // 2. Project (scoped to the team we just made)
      const projectResp = await createProject({
        variables: {
          input: {
            teamId: team.id,
            name: state.projectName.trim(),
            slug: state.projectSlug,
            description: state.projectDescription || null,
          },
        },
      });
      const projectResult = projectResp.data?.createProject;
      if (!projectResult?.ok || !projectResult.data) {
        const msg = projectResult?.errors?.[0]?.message ?? t("toast.projectFailed");
        setState((prev) => ({ ...prev, submitting: false, submitError: msg, step: 2 }));
        toast.error(msg);
        return;
      }
      const project = projectResult.data;

      // 3. Register app (only when the operator actually picked a repo;
      //    the wizard treats the picker as optional so the team /
      //    project still land even without a repo).
      if (state.sourceRepo && state.sourceConnectionId) {
        const appSlug = state.projectSlug;
        await registerApp({
          variables: {
            input: {
              projectId: project.id,
              name: state.projectName.trim(),
              slug: appSlug,
              description: state.projectDescription || null,
              sourceKind: "github",
              sourceRepo: state.sourceRepo,
              sourceUrl: state.sourceUrl || null,
              defaultBranch: state.sourceDefaultBranch || null,
            },
          },
        }).catch((err: unknown) => {
          // Soft failure: the team + project landed; surface a
          // warning but still close + mark complete so the operator
          // can register the app manually from /apps/new without the
          // wizard reopening on the next visit.
          const msg = err instanceof Error ? err.message : t("toast.appWarning");
          toast.warning(msg);
        });
      }

      // 4. Mark onboarding complete (idempotent on the backend)
      await markComplete({ variables: { input: { skip: false } } });

      toast.success(t("toast.success"));
      setState((prev) => ({ ...prev, submitting: false }));
      onOpenChange(false);
      onComplete?.();
    } catch (err) {
      const message = err instanceof Error ? err.message : t("toast.submitFailed");
      setState((prev) => ({ ...prev, submitting: false, submitError: message }));
      toast.error(message);
    }
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
          {state.step === 3 && <RepoStep state={state} setState={setState} />}
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
