import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { DeployStrategyStep } from "@/components/onboarding/DeployStrategyStep";
import {
  DEFAULT_ONBOARDING_STATE,
  type OnboardingWizardState,
} from "@/components/onboarding/types";

const meta: Meta = { title: "Screens/Onboarding/DeployStrategyStep" };
export default meta;

function Demo({ initial = {} }: { initial?: Partial<OnboardingWizardState> }) {
  const [state, setState] = React.useState<OnboardingWizardState>({
    ...DEFAULT_ONBOARDING_STATE,
    ...initial,
  });
  return <DeployStrategyStep state={state} setState={setState} />;
}
export const Default: StoryObj = { render: () => <Demo /> };
/** The review lists the registered app's lifetime and what expiry does to its data. */
export const TemporaryApp: StoryObj = {
  render: () => (
    <Demo
      initial={{
        teamName: "Commerce",
        teamSlug: "commerce",
        projectName: "Storefront",
        projectSlug: "storefront",
        sourceConnectionId: "c1",
        sourceRepo: "conflict/checkout",
        expiresIn: "7d",
      }}
    />
  ),
};
