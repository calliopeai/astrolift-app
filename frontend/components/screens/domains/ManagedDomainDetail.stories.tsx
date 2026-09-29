import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DOMAIN_LONG, DOMAIN_UNPROVISIONED, MANAGED_DOMAIN } from "./domains-environments.fixtures";
import { ManagedDomainDetail } from "./ManagedDomainDetail";

const meta: Meta = {
  title: "Screens/Domains/ManagedDomainDetail",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <ManagedDomainDetail {...MANAGED_DOMAIN} /> };

export const Loading: Story = {
  render: () => <ManagedDomainDetail {...MANAGED_DOMAIN} loading domain={null} />,
};

/**
 * The detail has no empty or error state of its own: an unknown id, a
 * failed query, and a deep link past the list all resolve to not found.
 */
export const NotFound: Story = {
  render: () => <ManagedDomainDetail {...MANAGED_DOMAIN} id="no-such-domain-id" domain={null} />,
};

/** Seeded, never provisioned, not a default for anything. */
export const Unprovisioned: Story = {
  render: () => <ManagedDomainDetail {...MANAGED_DOMAIN} domain={DOMAIN_UNPROVISIONED} />,
};

export const LongStrings: Story = {
  render: () => <ManagedDomainDetail {...MANAGED_DOMAIN} domain={DOMAIN_LONG} />,
};
