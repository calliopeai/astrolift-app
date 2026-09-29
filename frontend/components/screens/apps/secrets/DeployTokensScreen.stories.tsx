import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { userEvent, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";
import { AppTabsView } from "@/components/screens/apps/detail/AppTabs";

import { DeployTokensScreen } from "./DeployTokensScreen";
import { TOKEN_REVEAL, TOKENS_LONG, TOKENS_SCREEN } from "./app-secrets-tokens.fixtures";
import { APP_DEPLOY_TOKENS_LIST } from "./deploy-tokens-list";

const meta: Meta = { title: "Screens/Apps/Secrets/DeployTokensScreen" };
export default meta;

type Story = StoryObj;

function Screen({ initial, ...props }: typeof TOKENS_SCREEN & { initial?: Partial<ListState> }) {
  const list = useLocalListState(APP_DEPLOY_TOKENS_LIST, initial);
  return (
    <DeployTokensScreen
      {...props}
      list={list}
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
  render: () => <Screen {...TOKENS_SCREEN} rows={[]} loading totalCount={null} />,
};

export const Empty: Story = {
  render: () => <Screen {...TOKENS_SCREEN} rows={[]} totalCount={0} />,
};

export const NoSearchMatch: Story = {
  render: () => <Screen {...TOKENS_SCREEN} rows={[]} totalCount={0} initial={{ q: "zzz" }} />,
};

/** More tokens than one page: Older is live on the cursor. */
export const MorePages: Story = {
  render: () => <Screen {...TOKENS_SCREEN} nextCursor="cursor-2" totalCount={60} />,
};

export const LoadFailed: Story = {
  render: () => <Screen {...TOKENS_SCREEN} rows={[]} error={{ message: "upstream timed out" }} />,
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

export const W768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Screen {...TOKENS_LONG} />
    </div>
  ),
};
