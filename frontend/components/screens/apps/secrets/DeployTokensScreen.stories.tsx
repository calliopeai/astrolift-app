import { NextIntlClientProvider } from "next-intl";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import pt from "@/messages/pt-BR.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

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

/** No confirmation is possible before the app-scoped configuration read completes. */
export const RotationMetadataLoading: Story = {
  render: () => <Screen {...TOKENS_SCREEN} onLoadRotationGrace={() => new Promise(() => {})} />,
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getAllByRole("button", { name: "Rotate" })[0]);
    const dialog = within(document.body).getByRole("alertdialog");
    await expect(within(dialog).getByRole("status")).toHaveTextContent("Reading the configured");
    await expect(within(dialog).getByRole("button", { name: "Rotate token" })).toBeDisabled();
  },
};

export const RotationMetadataUnavailable: Story = {
  render: () => (
    <Screen
      {...TOKENS_SCREEN}
      onLoadRotationGrace={async () => {
        throw new Error("Permission denied");
      }}
    />
  ),
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getAllByRole("button", { name: "Rotate" })[0]);
    const dialog = within(document.body).getByRole("alertdialog");
    await expect(await within(dialog).findByRole("alert")).toHaveTextContent("could not be read");
    await expect(within(dialog).getByRole("button", { name: "Rotate token" })).toBeDisabled();
  },
};

/** Non-default, non-whole-minute windows retain their exact duration. */
export const RotationConfiguredWindow: Story = {
  render: () => <Screen {...TOKENS_SCREEN} onLoadRotationGrace={async () => 90} />,
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getAllByRole("button", { name: "Rotate" })[0]);
    const dialog = within(document.body).getByRole("alertdialog");
    await expect(await within(dialog).findByText(/Configured grace window: 1m 30s/)).toBeVisible();
    await expect(within(dialog).getByRole("button", { name: "Rotate token" })).toBeEnabled();
  },
};

export const Spanish: Story = {
  render: () => (
    <NextIntlClientProvider locale="es" messages={es} timeZone="UTC">
      <Screen {...TOKENS_SCREEN} reveal={{ ...TOKEN_REVEAL, rotationGraceSeconds: 90 }} />
    </NextIntlClientProvider>
  ),
};

export const French: Story = {
  render: () => (
    <NextIntlClientProvider locale="fr" messages={fr} timeZone="UTC">
      <Screen {...TOKENS_SCREEN} reveal={{ ...TOKEN_REVEAL, rotationGraceSeconds: 90 }} />
    </NextIntlClientProvider>
  ),
};

export const German: Story = {
  render: () => (
    <NextIntlClientProvider locale="de" messages={de} timeZone="UTC">
      <Screen {...TOKENS_SCREEN} reveal={{ ...TOKEN_REVEAL, rotationGraceSeconds: 90 }} />
    </NextIntlClientProvider>
  ),
};

export const Portuguese: Story = {
  render: () => (
    <NextIntlClientProvider locale="pt-BR" messages={pt} timeZone="UTC">
      <Screen {...TOKENS_SCREEN} reveal={{ ...TOKEN_REVEAL, rotationGraceSeconds: 90 }} />
    </NextIntlClientProvider>
  ),
};

export const Japanese: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja} timeZone="UTC">
      <Screen {...TOKENS_SCREEN} reveal={{ ...TOKEN_REVEAL, rotationGraceSeconds: 90 }} />
    </NextIntlClientProvider>
  ),
};

export const Korean: Story = {
  render: () => (
    <NextIntlClientProvider locale="ko" messages={ko} timeZone="UTC">
      <Screen {...TOKENS_SCREEN} reveal={{ ...TOKEN_REVEAL, rotationGraceSeconds: 90 }} />
    </NextIntlClientProvider>
  ),
};

export const Chinese: Story = {
  render: () => (
    <NextIntlClientProvider locale="zh-Hans" messages={zh} timeZone="UTC">
      <Screen {...TOKENS_SCREEN} reveal={{ ...TOKEN_REVEAL, rotationGraceSeconds: 90 }} />
    </NextIntlClientProvider>
  ),
};
