import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import { ALERT_SUBSCRIPTIONS_LIST } from "./alert-subscriptions-list";
import { AlertSubscriptionsView, type AlertSubscriptionsViewProps } from "./AlertSubscriptions";
import { LONG_APPS, alertProps } from "./settings-security-notifications.fixtures";

const meta: Meta = {
  title: "Screens/Settings/Security/AlertSubscriptions",
};
export default meta;

type Story = StoryObj;

type Props = Partial<Omit<AlertSubscriptionsViewProps, "list">> & { initial?: Partial<ListState> };

/** The view with its list state in memory, as the hook keeps it. */
function AlertSubscriptionsStory({ initial, ...over }: Props) {
  const list = useLocalListState(ALERT_SUBSCRIPTIONS_LIST, initial);
  return <AlertSubscriptionsView {...alertProps(over)} list={list} />;
}

export const Full: Story = { render: () => <AlertSubscriptionsStory /> };

export const Loading: Story = { render: () => <AlertSubscriptionsStory rows={[]} loading /> };

export const Empty: Story = { render: () => <AlertSubscriptionsStory rows={[]} totalCount={0} /> };

/** A search that matches no app. */
export const NoMatches: Story = {
  render: () => <AlertSubscriptionsStory rows={[]} totalCount={0} initial={{ q: "zzz" }} />,
};

/** The apps page failed to load. */
export const LoadFailed: Story = {
  render: () => (
    <AlertSubscriptionsStory
      rows={[]}
      error={{ message: "Response not successful: Received status code 503" }}
    />
  ),
};

/** Mine: the apps you get at least one alert for, with the note saying how. */
export const Mine: Story = { render: () => <AlertSubscriptionsStory initial={{ view: "mine" }} /> };

/** A mutation or the subscription list in flight: every control is disabled. */
export const Busy: Story = { render: () => <AlertSubscriptionsStory busy /> };

export const LongStrings: Story = {
  render: () => (
    <AlertSubscriptionsStory rows={LONG_APPS} totalCount={LONG_APPS.length} nextCursor="c2" />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <AlertSubscriptionsStory rows={LONG_APPS} />
    </div>
  ),
};
