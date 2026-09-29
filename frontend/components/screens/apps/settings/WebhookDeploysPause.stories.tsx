import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { LONG, WEBHOOK_DEPLOYS, WEBHOOK_DEPLOYS_PAUSED } from "./app-settings-members.fixtures";
import { WebhookDeploysPauseView } from "./WebhookDeploysPause";

const meta: Meta<typeof WebhookDeploysPauseView> = {
  title: "Screens/Apps/Settings/WebhookDeploysPause",
  component: WebhookDeploysPauseView,
  args: WEBHOOK_DEPLOYS,
};
export default meta;

type Story = StoryObj<typeof WebhookDeploysPauseView>;

/** Live: webhook deploys flowing. */
export const Full: Story = {};

export const Paused: Story = { args: WEBHOOK_DEPLOYS_PAUSED };

/** A resume in flight. */
export const Loading: Story = { args: { ...WEBHOOK_DEPLOYS_PAUSED, resuming: true } };

/** Paused with no reason, actor or time recorded. */
export const Empty: Story = {
  args: { ...WEBHOOK_DEPLOYS_PAUSED, pausedAt: null, pausedByEmail: null, pauseReason: "" },
};

/** No error state; a failed pause is a toast. Shown: the confirm dialog. */
export const ConfirmPause: Story = {
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: /Pause/ }));
    await expect(await within(document.body).findByRole("alertdialog")).toBeInTheDocument();
  },
};

export const LongStrings: Story = {
  args: { ...WEBHOOK_DEPLOYS_PAUSED, pausedByEmail: `${LONG}@example.com`, pauseReason: LONG },
};
