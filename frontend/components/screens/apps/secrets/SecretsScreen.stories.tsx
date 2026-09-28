import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { userEvent, within } from "storybook/test";

import { AppTabsView } from "@/components/screens/apps/detail/AppTabs";
import { PushAndRotateButtonView } from "@/components/screens/apps/overview/CiSetupSection";

import { SecretHistoryPanelView } from "./SecretHistoryPanel";
import { SecretsScreen } from "./SecretsScreen";
import {
  HISTORY,
  SECRETS_EMPTY,
  SECRETS_LOADING,
  SECRETS_LONG,
  SECRETS_SCREEN,
} from "./app-secrets-tokens.fixtures";

const meta: Meta = { title: "Screens/Apps/Secrets/SecretsScreen" };
export default meta;

type Story = StoryObj;

function Screen(props: typeof SECRETS_SCREEN) {
  return (
    <SecretsScreen
      {...props}
      tabs={
        <AppTabsView
          slug={props.slug}
          basePath="/apps"
          pathname={`/apps/${props.slug}/secrets`}
          active="secrets"
        />
      }
      pushToGitHub={
        <PushAndRotateButtonView
          pushing={false}
          onPushAndRotate={async () => {}}
          variant="outline"
          label="Push to GitHub"
        />
      }
      renderHistory={(secretKey) => <SecretHistoryPanelView {...HISTORY} secretKey={secretKey} />}
    />
  );
}

/** Literal, bundle and managed keys; expiry, scope and pending-approval chips; banners. */
export const Full: Story = { render: () => <Screen {...SECRETS_SCREEN} /> };

export const Loading: Story = { render: () => <Screen {...SECRETS_LOADING} /> };

export const Empty: Story = { render: () => <Screen {...SECRETS_EMPTY} /> };

/** The secrets query failed with nothing cached: the error and Retry sit in the table's frame. */
export const QueryFailed: Story = {
  render: () => (
    <Screen
      {...SECRETS_EMPTY}
      secretsError={{
        name: "ApolloError",
        message: "Network request failed: 503 Service Unavailable",
      }}
    />
  ),
};

export const Revealed: Story = {
  render: () => (
    <Screen {...SECRETS_SCREEN} revealedValues={{ "s-1": "postgres://app:hunter2@db:5432/shop" }} />
  ),
};

export const Revealing: Story = { render: () => <Screen {...SECRETS_SCREEN} revealingId="s-1" /> };

export const InlineEditing: Story = {
  render: () => (
    <Screen
      {...SECRETS_SCREEN}
      revealedValues={{ "s-1": "postgres://app:hunter2@db:5432/shop" }}
      editingId="s-1"
    />
  ),
};

export const FilteredToEnvironment: Story = {
  render: () => (
    <Screen
      {...SECRETS_SCREEN}
      envName="staging"
      secrets={SECRETS_SCREEN.secrets.filter((s) => s.environmentName === "staging")}
      attachments={SECRETS_SCREEN.attachments.filter((a) => a.environmentName === "staging")}
    />
  ),
};

export const NewSecretSheet: Story = {
  render: () => <Screen {...SECRETS_SCREEN} />,
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: /New secret/i }));
  },
};

/** A key's audit history opens in a side sheet. */
export const HistorySheet: Story = {
  render: () => <Screen {...SECRETS_SCREEN} />,
  play: async ({ canvasElement }) => {
    const [first] = within(canvasElement).getAllByRole("button", { name: "History" });
    await userEvent.click(first);
  },
};

export const LongStrings: Story = { render: () => <Screen {...SECRETS_LONG} /> };

export const W768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Screen {...SECRETS_LONG} />
    </div>
  ),
};
