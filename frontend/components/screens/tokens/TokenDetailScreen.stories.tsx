import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  TOKEN_DETAIL,
  TOKEN_DETAIL_LONG,
  TOKENS,
} from "@/components/screens/teams/teams-tokens.fixtures";

import { TokenDetailScreen } from "./TokenDetailScreen";

const meta: Meta = { title: "Screens/Tokens/TokenDetailScreen" };
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <TokenDetailScreen {...TOKEN_DETAIL} /> };

/** An org-wide admin token with no expiry and no usage metadata. */
export const OrgWideAdmin: Story = {
  render: () => <TokenDetailScreen id={TOKENS[1]!.id} token={TOKENS[1]!} loading={false} />,
};

/** Revoked, with no scopes: the empty scopes card. */
export const RevokedNoScopes: Story = {
  render: () => <TokenDetailScreen id={TOKENS[2]!.id} token={TOKENS[2]!} loading={false} />,
};

export const Loading: Story = {
  render: () => <TokenDetailScreen id={TOKEN_DETAIL.id} token={null} loading />,
};

/** The id matched no token: the screen's only error state. */
export const NotFound: Story = {
  render: () => <TokenDetailScreen id={TOKEN_DETAIL.id} token={null} loading={false} />,
};

export const LongStrings: Story = { render: () => <TokenDetailScreen {...TOKEN_DETAIL_LONG} /> };
