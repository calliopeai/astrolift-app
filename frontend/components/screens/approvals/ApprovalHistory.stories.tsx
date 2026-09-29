import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ApprovalHistoryPanel } from "./ApprovalHistory";
import { HISTORY, HISTORY_ENTRIES, LONG } from "./approvals-a.fixtures";

const meta: Meta = {
  title: "Screens/Approvals/ApprovalHistory",
  decorators: [
    (Story) => (
      <div className="max-w-sm p-4">
        <Story />
      </div>
    ),
  ],
};
export default meta;

type Story = StoryObj;

/** Started, approved, then rejected by magic-link token. */
export const Full: Story = {
  render: () => <ApprovalHistoryPanel {...HISTORY} />,
};

export const Loading: Story = {
  render: () => <ApprovalHistoryPanel entries={[]} loading />,
};

/** No entries yet. The panel has no error state; a failed query renders this too. */
export const Empty: Story = {
  render: () => <ApprovalHistoryPanel entries={[]} loading={false} />,
};

export const LongStrings: Story = {
  render: () => (
    <ApprovalHistoryPanel
      loading={false}
      entries={HISTORY_ENTRIES.map((e) => ({ ...e, actorDisplay: LONG, reason: LONG }))}
    />
  ),
};
