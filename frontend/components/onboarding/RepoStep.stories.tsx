import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import type { TemporaryAppPolicyState } from "@/components/screens/apps/temporary-app-model";
import {
  REPO_DATA,
  TEMPORARY_ONLY_POLICY,
  TEMPORARY_POLICY,
} from "@/components/onboarding/fixtures";
import { RepoStep, type RepoStepData } from "@/components/onboarding/RepoStep";
import {
  DEFAULT_ONBOARDING_STATE,
  type OnboardingWizardState,
} from "@/components/onboarding/types";

const meta: Meta = { title: "Screens/Onboarding/RepoStep" };
export default meta;

function Demo({
  data,
  temporary = TEMPORARY_POLICY,
  initial = {},
}: {
  data: RepoStepData;
  temporary?: TemporaryAppPolicyState;
  initial?: Partial<OnboardingWizardState>;
}) {
  const [state, setState] = React.useState<OnboardingWizardState>({
    ...DEFAULT_ONBOARDING_STATE,
    step: 3,
    sourceConnectionId: data.connections[0]?.id ?? "",
    ...initial,
  });
  return <RepoStep state={state} setState={setState} temporary={temporary} {...data} />;
}

/** A picked repo, so the step asks for its app's lifetime. */
const PICKED = { sourceRepo: "conflict/checkout" };

export const WithRepos: StoryObj = { render: () => <Demo data={REPO_DATA} /> };
export const Loading: StoryObj = {
  render: () => <Demo data={{ ...REPO_DATA, connections: [], connectionsLoading: true }} />,
};
export const NoConnection: StoryObj = {
  render: () => <Demo data={{ ...REPO_DATA, connections: [], repos: null }} />,
};
export const TemporaryApp: StoryObj = {
  render: () => (
    <Demo data={REPO_DATA} initial={{ ...PICKED, expiresIn: "7d", deleteDataOnExpiry: true }} />
  ),
};
export const LifetimeRequired: StoryObj = {
  render: () => <Demo data={REPO_DATA} temporary={TEMPORARY_ONLY_POLICY} initial={PICKED} />,
};
export const LifetimePolicyLoading: StoryObj = {
  render: () => (
    <Demo
      data={REPO_DATA}
      temporary={{ rules: null, loading: true, failed: false }}
      initial={PICKED}
    />
  ),
};
export const LifetimePolicyUnavailable: StoryObj = {
  render: () => (
    <Demo
      data={REPO_DATA}
      temporary={{ rules: null, loading: false, failed: true, onRetry: () => {} }}
      initial={PICKED}
    />
  ),
};
