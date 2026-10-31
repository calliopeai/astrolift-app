import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { userEvent, within } from "storybook/test";
import { NextIntlClientProvider } from "next-intl";
import ja from "@/messages/ja.json";

import { PUBLIC_APPS } from "./fixtures";
import { PublicAppsView } from "./PublicApps";

const meta: Meta<typeof PublicAppsView> = {
  title: "Screens/Clusters/Settings/PublicApps",
  component: PublicAppsView,
  args: PUBLIC_APPS,
};
export default meta;

type Story = StoryObj<typeof PublicAppsView>;

/** The default: apps may be made public, each still behind the sign-in until its owner asks. */
export const Allowed: Story = {};

/** Every app on the cluster stays behind the sign-in. */
export const Refused: Story = { args: { allowed: false } };

/** On another edge an app's request does nothing; the section says so. */
export const NotEnvoy: Story = { args: { envoy: false } };

export const Saving: Story = { args: { saving: true } };

/** Stopping public apps re-gates them at once; the dialog says so. */
export const ConfirmStop: Story = {
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: "Stop public apps" }));
  },
};

/** A refused change keeps the dialog open with the server's reason. */
export const ChangeRefused: Story = {
  args: {
    onApply: async () => {
      throw new Error("saved, but some apps' edge routes could not be updated: c-1: forbidden");
    },
  },
  play: ConfirmStop.play,
};

export const Japanese: Story = {
  args: { envoy: false },
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="ja" messages={ja} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
