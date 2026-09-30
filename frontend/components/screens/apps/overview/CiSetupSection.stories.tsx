import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import {
  CI_SETUP,
  CI_SETUP_AGENT,
  CI_SETUP_APAC,
  CI_SETUP_EMPTY,
  CI_SETUP_EU,
  CI_SETUP_LOADING,
  CI_SETUP_LONG,
  CI_SETUP_PROBLEMS,
  CI_SETUP_UNAVAILABLE,
} from "./app-ci-observability-section.fixtures";
import { CiSetupSectionView, PushAndRotateButtonView } from "./CiSetupSection";

const meta: Meta = { title: "Screens/Apps/Overview/CiSetupSection" };
export default meta;

type Story = StoryObj;

/** AWS app, fully provisioned, workflow in sync, secrets validated. */
export const Full: Story = {
  render: () => <CiSetupSectionView {...CI_SETUP} />,
};

/** Platform API URL loading; validate and sync in flight. */
export const Loading: Story = {
  render: () => <CiSetupSectionView {...CI_SETUP_LOADING} />,
};

/** Nothing provisioned yet; no runnable AWS workflow is invented. */
export const Empty: Story = {
  render: () => <CiSetupSectionView {...CI_SETUP_EMPTY} />,
};

export const EuropeanRegion: Story = {
  render: () => <CiSetupSectionView {...CI_SETUP_EU} />,
};

export const AsiaPacificRegion: Story = {
  render: () => <CiSetupSectionView {...CI_SETUP_APAC} />,
};

export const WorkflowUnavailable: Story = {
  render: () => <CiSetupSectionView {...CI_SETUP_UNAVAILABLE} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByText("Managed CI workflow"));
    await expect(canvas.getByRole("button", { name: "Copy workflow YAML" })).toBeDisabled();
    await expect(canvas.getByText(/Workflow unavailable/)).toBeInTheDocument();
  },
};

/**
 * No error state of its own (failures surface as toasts). The closest:
 * a GCP app whose workflow drifted both ways, with stale and missing
 * secrets; the comparison is opened by the play function.
 */
export const DriftAndMissingSecrets: Story = {
  render: () => <CiSetupSectionView {...CI_SETUP_PROBLEMS} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByText(/Compare the repo's file/));
    await expect(canvas.getByText("In the repo")).toBeInTheDocument();
  },
};

/** Azure app with overlong slug, registry and credential values. */
export const LongStrings: Story = {
  render: () => <CiSetupSectionView {...CI_SETUP_LONG} />,
};

/** Agent package: source delivery instead of the image-build contract. */
export const AgentMode: Story = {
  render: () => <CiSetupSectionView {...CI_SETUP_AGENT} />,
};

/** The standalone button the Secrets tab uses. */
export const PushAndRotateButton: Story = {
  render: () => (
    <PushAndRotateButtonView {...CI_SETUP.pushAndRotate} variant="outline" label="Push to GitHub" />
  ),
};
