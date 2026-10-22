"use client";

import { useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { temporaryAppInput } from "@/components/screens/apps/temporary-app-model";
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
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { registersApp, type OnboardingStep, type OnboardingWizardState } from "./types";

export type OnboardingSubmitResult =
  | { ok: true }
  | {
      ok: false;
      message: string;
      /** The step to send the person back to. */ step?: OnboardingStep;
    };

export interface OnboardingActions {
  submit: (state: OnboardingWizardState) => Promise<OnboardingSubmitResult>;
  skip: () => Promise<boolean>;
}

/**
 * The wizard's writes: team, project, the optional app, and marking
 * onboarding complete (or skipped). The data half of OnboardingWizard.
 */
export function useOnboardingActions(organizationId: string): OnboardingActions {
  const t = useTranslations("onboarding");
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
  const [registerApp] = useMutation<{ registerApp: MutationResult<AstroliftRegisteredApp> }>(
    REGISTER_APP
  );

  async function skip(): Promise<boolean> {
    try {
      const { data } = await markComplete({ variables: { input: { skip: true } } });
      const ok = data?.markOnboardingComplete.ok ?? false;
      if (!ok) {
        toast.error(t("toast.skipFailed"));
        return false;
      }
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : t("toast.skipFailed");
      toast.error(message);
      return false;
    }
  }

  async function submit(state: OnboardingWizardState): Promise<OnboardingSubmitResult> {
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
        toast.error(msg);
        return { ok: false, message: msg, step: 1 };
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
        toast.error(msg);
        return { ok: false, message: msg, step: 2 };
      }
      const project = projectResult.data;

      // 3. Register app (only when the operator actually picked a repo;
      //    the wizard treats the picker as optional so the team /
      //    project still land even without a repo).
      if (registersApp(state)) {
        const appSlug = state.projectSlug;
        let appWarning: string | null = null;
        try {
          const { data } = await registerApp({
            variables: {
              input: {
                projectId: project.id,
                // Only a lifetime the operator set: a server without
                // temporary apps has no such input fields (#2433).
                ...(state.expiresIn.trim() ? temporaryAppInput(state) : {}),
                name: state.projectName.trim(),
                slug: appSlug,
                description: state.projectDescription || null,
                sourceKind: "github",
                sourceRepo: state.sourceRepo,
                sourceUrl: state.sourceUrl || null,
                defaultBranch: state.sourceDefaultBranch || null,
              },
            },
          });
          const result = data?.registerApp;
          if (!result?.ok) appWarning = result?.errors?.[0]?.message ?? t("toast.appWarning");
        } catch (err) {
          appWarning = err instanceof Error ? err.message : t("toast.appWarning");
        }
        // Soft failure, whether the server refused the app (its lifetime
        // policy, say) or the request failed: the team + project landed;
        // surface a warning but still close + mark complete so the
        // operator can register the app manually from /apps/new without
        // the wizard reopening on the next visit.
        if (appWarning) toast.warning(appWarning);
      }

      // 4. Mark onboarding complete (idempotent on the backend)
      await markComplete({ variables: { input: { skip: false } } });

      toast.success(t("toast.success"));
      return { ok: true };
    } catch (err) {
      const message = err instanceof Error ? err.message : t("toast.submitFailed");
      toast.error(message);
      return { ok: false, message };
    }
  }

  return { submit, skip };
}
