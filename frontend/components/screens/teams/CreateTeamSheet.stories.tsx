import { NextIntlClientProvider } from "next-intl";
import messages from "@/messages/fr.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { createTeamProps } from "../projects/projects-teams-dialogs.fixtures";

import { CreateTeamSheet } from "./CreateTeamSheet";

const meta: Meta = {
  title: "Screens/Teams/CreateTeamSheet",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Open: Story = { render: () => <CreateTeamSheet {...createTeamProps()} /> };

/** The create mutation is in flight. */
export const Creating: Story = {
  render: () => <CreateTeamSheet {...createTeamProps({ creating: true })} />,
};

/** No active organization resolved yet: the submit stays disabled. */
export const NoOrganization: Story = {
  render: () => <CreateTeamSheet {...createTeamProps({ hasOrg: false })} />,
};

export const Translated: Story = {
  ...Open,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="fr" messages={messages} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
