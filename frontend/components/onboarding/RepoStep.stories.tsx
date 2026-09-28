import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { REPO_DATA } from "@/components/onboarding/fixtures";
import { RepoStep, type RepoStepData } from "@/components/onboarding/RepoStep";
import {
  DEFAULT_ONBOARDING_STATE,
  type OnboardingWizardState,
} from "@/components/onboarding/types";

const meta: Meta = { title: "Screens/Onboarding/RepoStep" };
export default meta;

function Demo({ data }: { data: RepoStepData }) {
  const [state, setState] = React.useState<OnboardingWizardState>({
    ...DEFAULT_ONBOARDING_STATE,
    step: 3,
    sourceConnectionId: data.connections[0]?.id ?? "",
  });
  return <RepoStep state={state} setState={setState} {...data} />;
}

export const WithRepos: StoryObj = { render: () => <Demo data={REPO_DATA} /> };
export const Loading: StoryObj = {
  render: () => <Demo data={{ ...REPO_DATA, connections: [], connectionsLoading: true }} />,
};
export const NoConnection: StoryObj = {
  render: () => <Demo data={{ ...REPO_DATA, connections: [], repos: null }} />,
};
