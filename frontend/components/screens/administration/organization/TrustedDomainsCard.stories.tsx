import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import {
  trustedDomains,
  trustedDomainsEmpty,
  type TrustedDomainsFixture,
  trustedDomainsLoading,
  trustedDomainsLong,
  trustedDomainsRemoveFails,
} from "./fixtures";
import { TRUSTED_DOMAINS_LIST } from "./trusted-domains-list";
import { TrustedDomainsCard } from "./TrustedDomainsCard";

const meta: Meta = { title: "Screens/Administration/Organization/TrustedDomainsCard" };
export default meta;

type Story = StoryObj;

function Card({ initial, ...props }: TrustedDomainsFixture & { initial?: Partial<ListState> }) {
  const list = useLocalListState(TRUSTED_DOMAINS_LIST, initial);
  return <TrustedDomainsCard {...props} list={list} />;
}

export const Full: Story = { render: () => <Card {...trustedDomains} /> };

export const Loading: Story = { render: () => <Card {...trustedDomainsLoading} /> };

export const Empty: Story = { render: () => <Card {...trustedDomainsEmpty} /> };

export const EmptyFiltered: Story = {
  render: () => <Card {...trustedDomainsEmpty} initial={{ filters: { mode: "review" } }} />,
};

/** The allowlist failed to load: the list shows its retry state. */
export const LoadFailed: Story = {
  render: () => <Card {...trustedDomainsEmpty} error={{ message: "upstream timed out" }} />,
};

/** Add resolves false and remove throws, so the confirm dialog stays open. */
export const Error: Story = { render: () => <Card {...trustedDomainsRemoveFails} /> };

export const Busy: Story = { render: () => <Card {...trustedDomains} adding removing /> };

export const LongStrings: Story = { render: () => <Card {...trustedDomainsLong} /> };

/** The narrowest the web console goes (spec 44 §6). */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Card {...trustedDomainsLong} />
    </div>
  ),
};
