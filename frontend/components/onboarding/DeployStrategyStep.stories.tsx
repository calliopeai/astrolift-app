import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { DeployStrategyStep } from "@/components/onboarding/DeployStrategyStep";
import {
  DEFAULT_ONBOARDING_STATE,
  type OnboardingWizardState,
} from "@/components/onboarding/types";

const meta: Meta = { title: "Screens/Onboarding/DeployStrategyStep" };
export default meta;

function Demo() {
  const [state, setState] = React.useState<OnboardingWizardState>(DEFAULT_ONBOARDING_STATE);
  return <DeployStrategyStep state={state} setState={setState} />;
}
export const Default: StoryObj = { render: () => <Demo /> };
