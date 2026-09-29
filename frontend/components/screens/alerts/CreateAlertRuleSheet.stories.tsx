import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { CreateAlertRuleSheet } from "./CreateAlertRuleSheet";

const meta: Meta = {
  title: "Screens/Alerts/CreateAlertRuleSheet",
};
export default meta;

type Story = StoryObj;

/** The form's only states are idle and submitting; it has no load or error of its own. */
export const Open: Story = {
  render: () => (
    <CreateAlertRuleSheet open onOpenChange={() => {}} onSubmit={async () => true} busy={false} />
  ),
};

export const Submitting: Story = {
  render: () => (
    <CreateAlertRuleSheet open onOpenChange={() => {}} onSubmit={async () => false} busy />
  ),
};
