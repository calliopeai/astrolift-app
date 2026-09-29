import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { ProjectStep } from "@/components/onboarding/ProjectStep";
import {
  DEFAULT_ONBOARDING_STATE,
  type OnboardingWizardState,
} from "@/components/onboarding/types";

const meta: Meta = { title: "Screens/Onboarding/ProjectStep" };
export default meta;

function Demo() {
  const [state, setState] = React.useState<OnboardingWizardState>({
    ...DEFAULT_ONBOARDING_STATE,
    teamName: "Commerce",
    teamSlug: "commerce",
  });
  return <ProjectStep state={state} setState={setState} />;
}
export const Default: StoryObj = { render: () => <Demo /> };
