import en from "@/messages/en.json";
import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  TOKEN_DETAIL,
  TOKEN_DETAIL_LONG,
  TOKENS,
} from "@/components/screens/teams/teams-tokens.fixtures";

import { TokenDetailScreen } from "./TokenDetailScreen";

const meta: Meta = {
  decorators: [
    (Story) => (
      <NextIntlClientProvider
        locale="en"
        messages={en}
        timeZone="UTC"
        now={new Date("2026-09-30T12:00:00Z")}
      >
        <Story />
      </NextIntlClientProvider>
    ),
  ],
  title: "Screens/Tokens/TokenDetailScreen",
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <TokenDetailScreen {...TOKEN_DETAIL} /> };

/** An org-wide admin token with no expiry and no usage metadata. */
export const OrgWideAdmin: Story = {
  render: () => (
    <TokenDetailScreen {...TOKEN_DETAIL} id={TOKENS[1]!.id} token={TOKENS[1]!} loading={false} />
  ),
};

/** Revoked, with no scopes: the empty scopes card. */
export const RevokedNoScopes: Story = {
  render: () => (
    <TokenDetailScreen {...TOKEN_DETAIL} id={TOKENS[2]!.id} token={TOKENS[2]!} loading={false} />
  ),
};

export const Loading: Story = {
  render: () => <TokenDetailScreen {...TOKEN_DETAIL} id={TOKEN_DETAIL.id} token={null} loading />,
};

/** Successful metadata read with no matching token. */
export const NotFound: Story = {
  render: () => (
    <TokenDetailScreen {...TOKEN_DETAIL} id={TOKEN_DETAIL.id} token={null} loading={false} />
  ),
};

export const LongStrings: Story = { render: () => <TokenDetailScreen {...TOKEN_DETAIL_LONG} /> };

export const French: Story = {
  decorators: [
    (Story) => (
      <NextIntlClientProvider
        locale="fr"
        messages={fr}
        timeZone="Europe/Berlin"
        now={new Date("2026-09-30T12:00:00Z")}
      >
        <Story />
      </NextIntlClientProvider>
    ),
  ],
  render: () => <TokenDetailScreen {...TOKEN_DETAIL} />,
};
export const GermanReadFailed: Story = {
  decorators: [
    (Story) => (
      <NextIntlClientProvider
        locale="de"
        messages={de}
        timeZone="Europe/Berlin"
        now={new Date("2026-09-30T12:00:00Z")}
      >
        <Story />
      </NextIntlClientProvider>
    ),
  ],
  render: () => (
    <TokenDetailScreen {...TOKEN_DETAIL} token={null} error={new Error("RAW_READ_ERROR")} />
  ),
};
export const JapaneseNotFound: Story = {
  decorators: [
    (Story) => (
      <NextIntlClientProvider
        locale="ja"
        messages={ja}
        timeZone="Europe/Berlin"
        now={new Date("2026-09-30T12:00:00Z")}
      >
        <Story />
      </NextIntlClientProvider>
    ),
  ],
  render: () => <TokenDetailScreen {...TOKEN_DETAIL} token={null} />,
};
