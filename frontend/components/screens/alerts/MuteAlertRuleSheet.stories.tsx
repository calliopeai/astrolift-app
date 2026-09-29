import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG_RULE, RULES } from "./alerts.fixtures";
import { MuteAlertRuleSheet } from "./MuteAlertRuleSheet";

const meta: Meta = {
  title: "Screens/Alerts/MuteAlertRuleSheet",
};
export default meta;

type Story = StoryObj;

const noop = () => {};
const noopAsync = async () => {};

export const Open: Story = {
  render: () => (
    <MuteAlertRuleSheet target={RULES[0]} onOpenChange={noop} onSubmit={noopAsync} busy={false} />
  ),
};

export const Busy: Story = {
  render: () => (
    <MuteAlertRuleSheet target={RULES[0]} onOpenChange={noop} onSubmit={noopAsync} busy />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <MuteAlertRuleSheet target={LONG_RULE} onOpenChange={noop} onSubmit={noopAsync} busy={false} />
  ),
};
