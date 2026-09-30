import { NextIntlClientProvider, useTranslations } from "next-intl";
import ja from "@/messages/ja.json";
import es from "@/messages/es.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { userEvent, within } from "storybook/test";

import { useLocalListState } from "@/components/list/use-list-state";
import { AppTabsView } from "@/components/screens/apps/detail/AppTabs";
import { PushAndRotateButtonView } from "@/components/screens/apps/overview/CiSetupSection";

import { SecretHistoryPanelView } from "./SecretHistoryPanel";
import {
  APP_SECRET_BUNDLES_LIST,
  APP_SECRETS_LIST,
  selectBundles,
  selectSecrets,
} from "./secrets-list";
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

/** The screen with in-memory list state, its rows answered as the hook would. */
function Screen(props: typeof SECRETS_SCREEN) {
  const t = useTranslations("apps.secrets");
  const keysList = useLocalListState(APP_SECRETS_LIST);
  const bundlesList = useLocalListState(APP_SECRET_BUNDLES_LIST);
  const keys = selectSecrets(props.secrets, keysList.filters, keysList.state);
  const bundles = selectBundles(props.attachments, bundlesList.filters, bundlesList.state);
  return (
    <SecretsScreen
      {...props}
      keysList={keysList}
      keyRows={keys.rows}
      keyTotal={keys.totalCount}
      bundlesList={bundlesList}
      bundleRows={bundles.rows}
      bundleTotal={bundles.totalCount}
      sectionHref={(s) =>
        s === "bundles"
          ? `/apps/${props.slug}/secrets?section=bundles`
          : `/apps/${props.slug}/secrets`
      }
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
          label={t("pushToRepo")}
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

/** The Attached bundles section: the bundles on their own list. */
export const BundlesSection: Story = {
  render: () => <Screen {...SECRETS_SCREEN} section="bundles" />,
};

export const BundlesEmpty: Story = {
  render: () => <Screen {...SECRETS_EMPTY} section="bundles" />,
};

export const BundlesLoading: Story = {
  render: () => <Screen {...SECRETS_LOADING} section="bundles" />,
};

export const BundlesQueryFailed: Story = {
  render: () => (
    <Screen
      {...SECRETS_EMPTY}
      section="bundles"
      attachmentsError={{
        name: "ApolloError",
        message: "Network request failed: 503 Service Unavailable",
      }}
    />
  ),
};

export const BundlesLongStrings: Story = {
  render: () => <Screen {...SECRETS_LONG} section="bundles" />,
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

export const Spanish: Story = {
  render: () => (
    <NextIntlClientProvider locale="es" messages={es} timeZone="UTC">
      <Screen {...SECRETS_SCREEN} />
    </NextIntlClientProvider>
  ),
};
export const Japanese: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja} timeZone="UTC">
      <Screen {...SECRETS_SCREEN} />
    </NextIntlClientProvider>
  ),
};
