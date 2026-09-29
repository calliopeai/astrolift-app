import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { ACTIONS, REPO_DATA } from "@/components/onboarding/fixtures";
import { OnboardingWizard } from "@/components/onboarding/OnboardingWizard";
import {
  DEFAULT_ONBOARDING_STATE,
  type OnboardingStep,
  type OnboardingWizardState,
} from "@/components/onboarding/types";
import type { OnboardingActions } from "@/components/onboarding/use-onboarding-actions";

/** The first-run wizard, controlled: every step, and a failed submit. */
const meta: Meta = { title: "Screens/Onboarding/OnboardingWizard" };
export default meta;

function Demo({ step, actions = ACTIONS }: { step: OnboardingStep; actions?: OnboardingActions }) {
  const [state, setState] = React.useState<OnboardingWizardState>({
    ...DEFAULT_ONBOARDING_STATE,
    step,
    teamName: "Commerce",
    teamSlug: "commerce",
    projectName: "Storefront",
    projectSlug: "storefront",
  });
  return (
    <OnboardingWizard
      open
      onOpenChange={() => {}}
      state={state}
      setState={setState}
      actions={actions}
      repo={REPO_DATA}
    />
  );
}

export const Welcome: StoryObj = { render: () => <Demo step={1} /> };
export const Project: StoryObj = { render: () => <Demo step={2} /> };
export const Repo: StoryObj = { render: () => <Demo step={3} /> };
export const Strategy: StoryObj = { render: () => <Demo step={4} /> };
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
