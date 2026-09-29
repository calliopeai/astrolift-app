import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { FORCE_REDEPLOY, FORCE_REDEPLOY_PREVIEW, LONG } from "./app-settings-members.fixtures";
import { ForceRedeployView } from "./ForceRedeploy";

const meta: Meta<typeof ForceRedeployView> = {
  title: "Screens/Apps/Settings/ForceRedeploy",
  component: ForceRedeployView,
  args: FORCE_REDEPLOY,
};
export default meta;

type Story = StoryObj<typeof ForceRedeployView>;

async function openDialog(canvasElement: HTMLElement) {
  await userEvent.click(within(canvasElement).getByRole("button", { name: /redeploy/i }));
  await expect(await within(document.body).findByRole("alertdialog")).toBeInTheDocument();
}

/** The card, closed. */
export const Card: Story = {};

/** The confirm dialog with an in-flight deployment to interrupt. */
export const Full: Story = {
  play: async ({ canvasElement }) => openDialog(canvasElement),
};

export const Loading: Story = {
  args: { preview: null, previewLoading: true },
  play: async ({ canvasElement }) => openDialog(canvasElement),
};

/** Nothing in flight: the calm path. */
export const Empty: Story = {
  args: { preview: { ...FORCE_REDEPLOY_PREVIEW, inFlightDeployments: [] } },
  play: async ({ canvasElement }) => openDialog(canvasElement),
};

/** No error state; a failed recovery is a toast. Shown: the recovery running. */
export const Running: Story = { args: { loading: true } };

export const LongStrings: Story = {
  args: {
    appSlug: LONG,
    preview: {
      ...FORCE_REDEPLOY_PREVIEW,
      inFlightDeployments: FORCE_REDEPLOY_PREVIEW.inFlightDeployments.map((d) => ({
        ...d,
        environmentName: LONG,
        workloadSlug: LONG,
        imageTag: LONG,
        triggeredByDisplay: LONG,
      })),
    },
  },
  play: async ({ canvasElement }) => openDialog(canvasElement),
};
