import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG_RULE, RULES, RULE_DETAIL } from "./alerts.fixtures";
import { AlertRuleDetail } from "./AlertRuleDetail";

const meta: Meta = {
  title: "Screens/Alerts/AlertRuleDetail",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** A muted rule, which shows every overview row. */
export const Full: Story = { render: () => <AlertRuleDetail {...RULE_DETAIL} /> };

export const Unmuted: Story = {
  render: () => <AlertRuleDetail id={RULES[0].id} rule={RULES[0]} loading={false} />,
};

export const Inactive: Story = {
  render: () => <AlertRuleDetail id={RULES[2].id} rule={RULES[2]} loading={false} />,
};

export const Loading: Story = {
  render: () => <AlertRuleDetail {...RULE_DETAIL} rule={null} loading />,
};

/**
 * No such rule. This is also the closest real state to "empty" and "error":
 * the screen has no error view, a failed LIST_ALERT_RULES lands here.
 */
export const NotFound: Story = {
  render: () => <AlertRuleDetail {...RULE_DETAIL} rule={null} />,
};

export const LongStrings: Story = {
  render: () => (
    <AlertRuleDetail
      id={LONG_RULE.id}
      rule={{ ...LONG_RULE, managedServiceId: `ms-postgres-${LONG_RULE.targetId}` }}
      loading={false}
    />
  ),
};
