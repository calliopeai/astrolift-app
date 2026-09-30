import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import ja from "@/messages/ja.json";

import { DEPLOYER, LONG_ROLE, RELEASE_CAPTAIN, ROLES, TEAM_DEV, VIEWER } from "./fixtures";
import { RoleSummary } from "./RoleSummary";

/** A role in one line, what it allows in plain words, expandable to its matrix. */
const meta: Meta<typeof RoleSummary> = {
  title: "Access/RoleSummary",
  component: RoleSummary,
  parameters: { layout: "padded" },
  args: { role: DEPLOYER },
  decorators: [(Story) => <div className="max-w-2xl">{Story()}</div>],
};
export default meta;

type Story = StoryObj<typeof RoleSummary>;

export const FutureScope: Story = {
  args: { role: { ...DEPLOYER, scopeLevel: "CUSTOM_SCOPE" as typeof DEPLOYER.scopeLevel } },
};

/** The role's own description wins. */
export const WithDescription: Story = {};

/** No description: the summary is built from its slugs. */
export const ReadOnlyRole: Story = { args: { role: VIEWER } };

export const Expanded: Story = { args: { role: TEAM_DEV, defaultOpen: true } };

/** A custom role, diffed against the built-in it came from. */
export const CustomWithDiff: Story = {
  args: { role: RELEASE_CAPTAIN, base: TEAM_DEV, defaultOpen: true },
};

export const NotExpandable: Story = { args: { role: TEAM_DEV, expandable: false } };

/** No permissions, and an empty catalog (the catalog has not loaded). */
export const Empty: Story = {
  args: { role: { ...VIEWER, permissions: [] }, catalog: [], defaultOpen: true },
};

/** As the role step lists them. */
export const List: Story = {
  render: () => (
    <div className="flex flex-col divide-y rounded-md border">
      {ROLES.map((r) => (
        <RoleSummary key={r.id} role={r} className="p-3" />
      ))}
    </div>
  ),
};

export const LongStrings: Story = { args: { role: LONG_ROLE, defaultOpen: true } };

export const Width768: Story = {
  decorators: [
    (Story) => (
      <div style={{ width: 768 }} className="overflow-hidden border p-4">
        {Story()}
      </div>
    ),
  ],
  args: { role: RELEASE_CAPTAIN, base: TEAM_DEV, defaultOpen: true },
};

export const Japanese: Story = {
  args: { role: VIEWER, defaultOpen: true },
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="ja" messages={ja}>
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
