import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { userEvent, within } from "storybook/test";

import { fakeController } from "@/components/data-table/fixtures";
import { AppTabsView } from "@/components/screens/apps/detail/AppTabs";

import { DeployTokensScreen } from "./DeployTokensScreen";
import { TOKEN_REVEAL, TOKENS_LONG, TOKENS_SCREEN } from "./app-secrets-tokens.fixtures";
import type { DeployToken } from "./secrets.types";

const meta: Meta = { title: "Screens/Apps/Secrets/DeployTokensScreen" };
export default meta;

type Story = StoryObj;

function Screen(props: typeof TOKENS_SCREEN) {
  return (
    <DeployTokensScreen
      {...props}
      tabs={
        <AppTabsView
          slug={props.slug}
          basePath="/apps"
          pathname={`/apps/${props.slug}/tokens`}
          active="secrets"
        />
      }
    />
  );
}

/** Scoped, all-scopes and revoked tokens; hover a last-used stamp for IP + agent. */
export const Full: Story = { render: () => <Screen {...TOKENS_SCREEN} /> };

export const Loading: Story = {
  render: () => (
    <Screen {...TOKENS_SCREEN} table={fakeController<DeployToken>({ state: "loading" })} />
  ),
};

export const Empty: Story = {
  render: () => (
    <Screen {...TOKENS_SCREEN} table={fakeController<DeployToken>({ state: "empty" })} />
  ),
};

export const NoSearchMatch: Story = {
  render: () => (
    <Screen
      {...TOKENS_SCREEN}
      table={fakeController<DeployToken>({
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
      table={fakeController<DeployToken>({
        state: "error",
        error: new globalThis.Error("upstream timed out"),
      })}
    />
  ),
};

/** The one-time plaintext reveal after create or rotate. */
export const SecretRevealed: Story = {
  render: () => <Screen {...TOKENS_SCREEN} reveal={TOKEN_REVEAL} />,
};

export const CreateSheet: Story = {
  render: () => <Screen {...TOKENS_SCREEN} />,
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: /Create token/i }));
  },
};

export const LongStrings: Story = { render: () => <Screen {...TOKENS_LONG} /> };
