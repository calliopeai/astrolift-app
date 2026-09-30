import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import de from "@/messages/de.json";

import { RESYNC } from "./app-settings-members.fixtures";
import { ResyncSourceView } from "./ResyncSource";

const meta: Meta<typeof ResyncSourceView> = {
  title: "Screens/Apps/Settings/ResyncSource",
  component: ResyncSourceView,
  args: RESYNC,
};
export default meta;

type Story = StoryObj<typeof ResyncSourceView>;

export const Full: Story = {};

/** A resync in flight. */
export const Loading: Story = { args: { loading: true } };

/** Never resynced. */
export const Empty: Story = { args: { lastResyncAt: null } };

/** The section has no error state; a failed resync is a toast. Shown: the agent variant. */
export const AgentMode: Story = { args: { agentMode: true } };

/** No free-text field here; the longest copy is the agent description. */
export const LongStrings: Story = { args: { agentMode: true, loading: true } };

export const GermanWidth768: Story = {
  render: (args) => (
    <NextIntlClientProvider locale="de" messages={de} timeZone="UTC">
      <div style={{ width: 768 }}>
        <ResyncSourceView {...args} />
      </div>
    </NextIntlClientProvider>
  ),
};
