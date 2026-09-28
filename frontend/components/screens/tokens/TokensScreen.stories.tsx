import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { userEvent, within } from "storybook/test";

import { fakeController } from "@/components/data-table/fixtures";
import {
  SCOPE_PICKER,
  TOKEN_CREATED,
  TOKENS_SCREEN,
  TOKENS_SCREEN_LONG,
} from "@/components/screens/teams/teams-tokens.fixtures";
import type { AstroliftApiToken } from "@/graphql/identity/identity.types";

import { ScopePicker } from "./ScopePicker";
import { TokensScreen } from "./TokensScreen";

const meta: Meta = { title: "Screens/Tokens/TokensScreen" };
export default meta;

type Story = StoryObj;

function Screen(props: typeof TOKENS_SCREEN) {
  return (
    <TokensScreen
      {...props}
      renderScopePicker={(picker) => <ScopePicker {...SCOPE_PICKER} {...picker} />}
    />
  );
}

/** Scoped, long-lived admin and revoked tokens; hover "Can do" or a last-used stamp. */
export const Full: Story = { render: () => <Screen {...TOKENS_SCREEN} /> };

export const Loading: Story = {
  render: () => (
    <Screen {...TOKENS_SCREEN} table={fakeController<AstroliftApiToken>({ state: "loading" })} />
  ),
};

export const Empty: Story = {
  render: () => (
    <Screen {...TOKENS_SCREEN} table={fakeController<AstroliftApiToken>({ state: "empty" })} />
  ),
};

export const NoSearchMatch: Story = {
  render: () => (
    <Screen
      {...TOKENS_SCREEN}
      table={fakeController<AstroliftApiToken>({
        state: "emptyFiltered",
        isFiltered: true,
        search: "zzz",
      })}
    />
  ),
};

export const LoadFailed: Story = {
  render: () => (
    <Screen
      {...TOKENS_SCREEN}
      table={fakeController<AstroliftApiToken>({
        state: "error",
        error: new globalThis.Error("upstream timed out"),
      })}
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
