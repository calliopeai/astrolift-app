import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import french from "@/messages/fr.json";

import { OWNERSHIP, OWNERSHIP_LONG } from "./app-overview-panels.fixtures";
import { OwnershipPanel } from "./OwnershipPanel";

const meta: Meta<typeof OwnershipPanel> = {
  title: "Screens/Apps/Overview/OwnershipPanel",
  component: OwnershipPanel,
  args: OWNERSHIP,
};
export default meta;

type Story = StoryObj<typeof OwnershipPanel>;

export const Full: Story = {};

export const Loading: Story = { args: { loading: true, accesses: [] } };

/** In no project, and no team besides the home team. */
export const Empty: Story = { args: { projectName: "", accesses: [] } };

export const QueryError: Story = {
  args: { accesses: [], error: "Network error: upstream timed out" },
};

export const LongStrings: Story = { args: OWNERSHIP_LONG };

export const At768: Story = {
  args: OWNERSHIP_LONG,
  render: (args) => (
    <div style={{ width: 768 }}>
      <OwnershipPanel {...args} />
    </div>
  ),
};

export const FrenchWidth768: Story = {
  args: { projectName: "", teamName: "", accesses: [] },
  render: (args) => (
    <NextIntlClientProvider locale="fr" messages={french}>
      <div style={{ width: 768 }}>
        <OwnershipPanel {...args} />
      </div>
    </NextIntlClientProvider>
  ),
};
