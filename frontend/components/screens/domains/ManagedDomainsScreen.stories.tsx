import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  DOMAIN_ACTIVE,
  DOMAIN_LONG,
  DOMAIN_PROVISIONING,
  DOMAIN_UNPROVISIONED,
  MANAGED_DOMAINS,
} from "./domains-environments.fixtures";
import { ManagedDomainsScreen } from "./ManagedDomainsScreen";

const meta: Meta = {
  title: "Screens/Domains/ManagedDomainsScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** One zone each: active with nameservers, provisioning, never provisioned. */
export const Full: Story = { render: () => <ManagedDomainsScreen {...MANAGED_DOMAINS} /> };

export const Loading: Story = {
  render: () => <ManagedDomainsScreen {...MANAGED_DOMAINS} loading domains={[]} />,
};

export const Empty: Story = {
  render: () => <ManagedDomainsScreen {...MANAGED_DOMAINS} domains={[]} />,
};

/**
 * The page has no error state (query and mutation errors surface as
 * toasts); the closest real one is every zone stuck short of active, with
 * revalidation unavailable on the zone that has no provisioning cluster.
 */
export const NotProvisioned: Story = {
  render: () => (
    <ManagedDomainsScreen
      {...MANAGED_DOMAINS}
      domains={[DOMAIN_PROVISIONING, DOMAIN_UNPROVISIONED]}
    />
  ),
};

/** A revalidate and a delete in flight: the row actions are disabled. */
export const Busy: Story = {
  render: () => <ManagedDomainsScreen {...MANAGED_DOMAINS} revalidating deleting />,
};

export const LongStrings: Story = {
  render: () => (
    <ManagedDomainsScreen {...MANAGED_DOMAINS} domains={[DOMAIN_LONG, DOMAIN_ACTIVE]} />
  ),
};
