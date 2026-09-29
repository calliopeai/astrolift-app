import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { MANAGED_SERVICES_HREF, SERVICES, SERVICES_LONG } from "./app-overview-cards-b.fixtures";
import { ManagedServicesPanel } from "./ManagedServicesPanel";

const meta: Meta<typeof ManagedServicesPanel> = {
  title: "Screens/Apps/Overview/ManagedServicesPanel",
  component: ManagedServicesPanel,
  args: { loading: false, services: SERVICES, managedServicesHref: MANAGED_SERVICES_HREF },
};
export default meta;

type Story = StoryObj<typeof ManagedServicesPanel>;

/** Failed first (with its error), then in flight, then active. */
export const Full: Story = {};

export const Loading: Story = { args: { loading: true, services: [] } };

export const Empty: Story = { args: { services: [] } };

export const QueryError: Story = {
  args: { services: [], error: "Network error: upstream timed out", onRetry: () => {} },
};

export const LongStrings: Story = { args: { services: SERVICES_LONG } };

export const At768: Story = {
  args: { services: SERVICES_LONG },
  render: (args) => (
    <div style={{ width: 768 }}>
      <ManagedServicesPanel {...args} />
    </div>
  ),
};
