import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import {
  DEFAULT_ONBOARDING_STATE,
  type OnboardingWizardState,
} from "@/components/onboarding/types";
import { WelcomeStep } from "@/components/onboarding/WelcomeStep";

const meta: Meta = { title: "Screens/Onboarding/WelcomeStep" };
export default meta;

function Demo() {
  const [state, setState] = React.useState<OnboardingWizardState>(DEFAULT_ONBOARDING_STATE);
  return <WelcomeStep state={state} setState={setState} />;
}
export const Default: StoryObj = { render: () => <Demo /> };
