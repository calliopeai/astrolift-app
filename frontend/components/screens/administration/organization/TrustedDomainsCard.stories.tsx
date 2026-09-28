import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  trustedDomains,
  trustedDomainsEmpty,
  trustedDomainsLoading,
  trustedDomainsLong,
  trustedDomainsRemoveFails,
} from "./fixtures";
import { TrustedDomainsCard } from "./TrustedDomainsCard";

const meta: Meta<typeof TrustedDomainsCard> = {
  title: "Screens/Administration/Organization/TrustedDomainsCard",
  component: TrustedDomainsCard,
  args: trustedDomains,
};
export default meta;

type Story = StoryObj<typeof TrustedDomainsCard>;

export const Full: Story = {};

export const Loading: Story = { args: trustedDomainsLoading };

export const Empty: Story = { args: trustedDomainsEmpty };

/** Add resolves false and remove throws, so the confirm dialog stays open. */
export const Error: Story = { args: trustedDomainsRemoveFails };

export const Busy: Story = { args: { ...trustedDomains, adding: true, removing: true } };

export const LongStrings: Story = { args: trustedDomainsLong };
