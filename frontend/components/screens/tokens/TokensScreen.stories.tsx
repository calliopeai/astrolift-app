import en from "@/messages/en.json";
import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { userEvent, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";
import { SCOPE_PICKER, TOKEN_CREATED } from "@/components/screens/teams/teams-tokens.fixtures";

import { ScopePicker } from "./ScopePicker";
import { useTranslations } from "next-intl";
import { localizedTokensList } from "./tokens-list";
import { type TokensData, TOKENS_SCREEN, TOKENS_SCREEN_LONG } from "./tokens.fixtures";
import { TokensScreen } from "./TokensScreen";

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
  title: "Screens/Tokens/TokensScreen",
};
export default meta;

type Story = StoryObj;

function Screen({ initial, ...props }: TokensData & { initial?: Partial<ListState> }) {
  const list = useLocalListState(localizedTokensList(useTranslations("apiKeys")), initial);
  return (
    <TokensScreen
      {...props}
      list={list}
      renderScopePicker={(picker) => <ScopePicker {...SCOPE_PICKER} {...picker} />}
    />
  );
}

/** Scoped, long-lived admin and revoked tokens; hover "Can do" or a last-used stamp. */
export const Full: Story = { render: () => <Screen {...TOKENS_SCREEN} /> };

export const Loading: Story = {
  render: () => <Screen {...TOKENS_SCREEN} rows={[]} loading />,
};

export const Empty: Story = {
  render: () => <Screen {...TOKENS_SCREEN} rows={[]} totalCount={0} />,
};

export const NoSearchMatch: Story = {
  render: () => <Screen {...TOKENS_SCREEN} rows={[]} initial={{ q: "zzz" }} />,
};

export const LoadFailed: Story = {
  render: () => <Screen {...TOKENS_SCREEN} rows={[]} error={{ message: "upstream timed out" }} />,
};

/** Revoked: the view's note says it narrows the newest page. */
export const RevokedView: Story = {
  render: () => (
    <Screen
      {...TOKENS_SCREEN}
      rows={TOKENS_SCREEN.rows.filter((t) => t.isRevoked)}
      initial={{ view: "revoked" }}
    />
  ),
};

/** The one-time plaintext reveal after create. */
export const TokenCreated: Story = {
  render: () => <Screen {...TOKENS_SCREEN} createdToken={TOKEN_CREATED} />,
};

export const CreateSheet: Story = {
  render: () => <Screen {...TOKENS_SCREEN} />,
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: /New token/i }));
  },
};

export const LongStrings: Story = { render: () => <Screen {...TOKENS_SCREEN_LONG} /> };

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Screen {...TOKENS_SCREEN_LONG} />
    </div>
  ),
};

export const GermanReveal768: Story = {
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
    <div style={{ width: 768 }}>
      <Screen {...TOKENS_SCREEN_LONG} />
    </div>
  ),
};
export const FrenchCreate: Story = {
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
  render: () => <Screen {...TOKENS_SCREEN} />,
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: "Nouveau jeton" }));
  },
};
