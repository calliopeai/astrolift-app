import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { ALERT_RULES_LIST } from "./alerts-list";
import { alertsProps, LONG_RULE, RULES } from "./alerts.fixtures";
import { AlertsScreen, type AlertsScreenProps } from "./AlertsScreen";

const meta: Meta = {
  title: "Screens/Alerts/AlertsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

function Screen({
  initial,
  ...over
}: Partial<Omit<AlertsScreenProps, "list">> & { initial?: Partial<ListState> }) {
  const list = useLocalListState(ALERT_RULES_LIST, initial);
  return <AlertsScreen {...alertsProps(over)} list={list} />;
}

export const Full: Story = { render: () => <Screen /> };

export const Loading: Story = {
  render: () => (
    <Screen rows={[]} loading totalCount={null} activeRuleCount={0} unresolvedCount={0} />
  ),
};

export const Empty: Story = {
  render: () => <Screen rows={[]} totalCount={0} activeRuleCount={0} unresolvedCount={0} />,
};

export const EmptyFiltered: Story = {
  render: () => <Screen rows={[]} initial={{ q: "zzz" }} />,
};

export const ErrorState: Story = {
  render: () => <Screen rows={[]} error={{ message: "upstream timed out" }} />,
};

/** Muted: narrowed on the page in hand, the note says so. */
export const Muted: Story = {
  render: () => (
    <Screen
      rows={RULES.filter((r) => r.activeMute)}
      totalCount={null}
      initial={{ view: "muted" }}
    />
  ),
};

/** A mutation in flight: every row action is disabled. */
export const Busy: Story = { render: () => <Screen busy /> };

export const LongStrings: Story = {
  render: () => (
    <Screen rows={[LONG_RULE, ...RULES]} totalCount={RULES.length + 1} nextCursor="c2" />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Screen rows={[LONG_RULE, ...RULES]} />
    </div>
  ),
};
