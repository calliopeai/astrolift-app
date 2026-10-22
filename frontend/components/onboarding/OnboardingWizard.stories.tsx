import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import {
  ACTIONS,
  REPO_DATA,
  TEMPORARY_ONLY_POLICY,
  TEMPORARY_POLICY,
} from "@/components/onboarding/fixtures";
import { OnboardingWizard } from "@/components/onboarding/OnboardingWizard";
import {
  DEFAULT_ONBOARDING_STATE,
  type OnboardingStep,
  type OnboardingWizardState,
} from "@/components/onboarding/types";
import type { OnboardingActions } from "@/components/onboarding/use-onboarding-actions";
import type { TemporaryAppPolicyState } from "@/components/screens/apps/temporary-app-model";

/** The first-run wizard, controlled: every step, a temporary app, and a failed submit. */
const meta: Meta = { title: "Screens/Onboarding/OnboardingWizard" };
export default meta;

function Demo({
  step,
  actions = ACTIONS,
  temporary = TEMPORARY_POLICY,
  initial = {},
}: {
  step: OnboardingStep;
  actions?: OnboardingActions;
  temporary?: TemporaryAppPolicyState;
  initial?: Partial<OnboardingWizardState>;
}) {
  const [state, setState] = React.useState<OnboardingWizardState>({
    ...DEFAULT_ONBOARDING_STATE,
    step,
    teamName: "Commerce",
    teamSlug: "commerce",
    projectName: "Storefront",
    projectSlug: "storefront",
    ...initial,
  });
  return (
    <OnboardingWizard
      open
      onOpenChange={() => {}}
      state={state}
      setState={setState}
      actions={actions}
      repo={REPO_DATA}
      temporary={temporary}
    />
  );
}

/** A picked repo, so a temporary app is registered with the team and project. */
const PICKED = { sourceConnectionId: "c1", sourceRepo: "conflict/checkout" };

export const Welcome: StoryObj = { render: () => <Demo step={1} /> };
export const Project: StoryObj = { render: () => <Demo step={2} /> };
export const Repo: StoryObj = { render: () => <Demo step={3} /> };
/** Next waits for a lifetime: this organization lets members create temporary apps only. */
export const RepoLifetimeRequired: StoryObj = {
  render: () => <Demo step={3} temporary={TEMPORARY_ONLY_POLICY} initial={PICKED} />,
};
export const Strategy: StoryObj = { render: () => <Demo step={4} /> };
export const StrategyTemporaryApp: StoryObj = {
  render: () => (
    <Demo step={4} initial={{ ...PICKED, expiresIn: "4h", deleteDataOnExpiry: true }} />
  ),
};
export const SubmitFails: StoryObj = {
  render: () => (
    <Demo
      step={4}
      actions={{
        ...ACTIONS,
        submit: async () => ({
          ok: false,
          message: "A team named commerce already exists.",
          step: 1,
        }),
      }}
    />
  ),
};
