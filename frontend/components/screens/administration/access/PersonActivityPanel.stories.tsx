import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { PersonActivityPanel } from "./PersonActivityPanel";
import { activityProps, EVENTS } from "./principal.fixtures";

const meta: Meta = {
  title: "Screens/Administration/Access/PersonActivityPanel",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <PersonActivityPanel {...activityProps()} /> };

export const Loading: Story = {
  render: () => <PersonActivityPanel {...activityProps({ items: [], loading: true })} />,
};

export const Empty: Story = {
  render: () => <PersonActivityPanel {...activityProps({ items: [], hasMore: false })} />,
};

export const LoadFailed: Story = {
  render: () => (
    <PersonActivityPanel
      {...activityProps({ items: [], error: { message: "upstream timed out after 30s" } })}
    />
  ),
};

export const LoadingOlder: Story = {
  render: () => <PersonActivityPanel {...activityProps({ loadingMore: true })} />,
};

/** Without audit_log.read: nothing is fetched; the tab says what would allow it. */
export const NotAllowed: Story = {
  render: () => <PersonActivityPanel {...activityProps({ allowed: false, items: [] })} />,
};

export const LongStrings: Story = {
  render: () => (
    <PersonActivityPanel
      {...activityProps({
        items: EVENTS.map((e) => ({ ...e, action: `${e.action}.${"x".repeat(80)}` })),
      })}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border p-4">
      <PersonActivityPanel {...activityProps()} />
    </div>
  ),
};
