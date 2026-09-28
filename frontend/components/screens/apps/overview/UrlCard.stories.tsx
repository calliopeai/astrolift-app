import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG, URL_CARD, URL_HEALTH } from "./app-overview-cards-a.fixtures";
import { UrlCardView } from "./UrlCard";
import { UrlHealthBadgeView } from "./UrlHealthBadge";

const meta: Meta<typeof UrlCardView> = {
  title: "Screens/Apps/Overview/UrlCard",
  component: UrlCardView,
  args: { ...URL_CARD, healthBadge: <UrlHealthBadgeView {...URL_HEALTH} /> },
};
export default meta;

type Story = StoryObj<typeof UrlCardView>;

/** Routable host with its live health pill. */
export const Full: Story = {};

/**
 * The card has no loading state of its own; the health pill's first check
 * is the closest.
 */
export const Loading: Story = {
  args: {
    healthBadge: (
      <UrlHealthBadgeView {...URL_HEALTH} health={undefined} isInitialLoading history={[]} />
    ),
  },
};

/** No public workload yet: "first deploy will publish it". */
export const Empty: Story = { args: { isRoutable: false, hasWorkload: false } };

/** Still provisioning. */
export const Provisioning: Story = { args: { isRoutable: false, isProvisioning: true } };

/** Ready with a workload the platform cannot route to yet. */
export const NotRoutable: Story = { args: { isRoutable: false, hasWorkload: true } };

/**
 * The card has no error state (a failed rename toasts). Closest real
 * state: the host is routable and the probe says it is down.
 */
export const Down: Story = {
  args: {
    healthBadge: (
      <UrlHealthBadgeView
        {...URL_HEALTH}
        health={{ ...URL_HEALTH.health!, status: "down", message: "connection refused" }}
      />
    ),
  },
};

/** Rename mutation in flight (enter edit mode with Edit to see the spinner). */
export const Saving: Story = { args: { saving: true } };

/** Viewer without app.update: no Edit affordance. */
export const ReadOnly: Story = { globals: { permissions: "none" } };

export const LongStrings: Story = {
  args: { subdomain: LONG, fullHost: `${LONG}.acme.astrolift.app` },
};

export const At768: Story = {
  args: { subdomain: LONG, fullHost: `${LONG}.acme.astrolift.app` },
  render: (args) => (
    <div style={{ width: 768 }}>
      <UrlCardView {...args} />
    </div>
  ),
};
