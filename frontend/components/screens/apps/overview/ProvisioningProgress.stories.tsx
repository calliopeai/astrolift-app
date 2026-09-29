import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { LONG, PROGRESS, PROVISIONING } from "./app-overview-banners.fixtures";
import { ProvisioningProgressView } from "./ProvisioningProgress";

const meta: Meta = { title: "Screens/Apps/Overview/ProvisioningProgress" };
export default meta;

type Story = StoryObj;

/** Two of five steps done, identity in progress. */
export const Full: Story = {
  render: () => <ProvisioningProgressView {...PROVISIONING} />,
};

/** No progress reported yet: the default three steps, first one active. */
export const Loading: Story = {
  render: () => <ProvisioningProgressView {...PROVISIONING} progress={null} />,
};

/** Not provisioning: the panel renders nothing. */
export const Empty: Story = {
  render: () => (
    <div data-testid="slot">
      <ProvisioningProgressView {...PROVISIONING} isProvisioning={false} />
    </div>
  ),
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByTestId("slot")).toBeEmptyDOMElement();
  },
};

/** The panel has no error state (failure shows elsewhere); closest is the last step. */
export const ErrorState: Story = {
  render: () => (
    <ProvisioningProgressView
      {...PROVISIONING}
      progress={{
        ...PROGRESS,
        currentStep: "ready",
        completed: ["registry", "namespace", "identity", "managed_services"],
      }}
    />
  ),
};

/** An unknown step key falls back to the raw key. */
export const LongStrings: Story = {
  render: () => (
    <ProvisioningProgressView
      {...PROVISIONING}
      progress={{ ...PROGRESS, currentStep: LONG, totalSteps: [...PROGRESS.totalSteps, LONG] }}
    />
  ),
};
