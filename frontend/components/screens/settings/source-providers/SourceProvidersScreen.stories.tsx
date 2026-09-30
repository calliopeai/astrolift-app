import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";
import { expect, userEvent, within } from "storybook/test";
import { Button } from "@/components/ui/button";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";
import { useLocalSettingsSection } from "@/components/settings/use-settings-section";
import type { AstroliftSourceConnection } from "@/graphql/scm/scm.types";

import { AddClientIdSheet } from "./AddClientIdSheet";
import { GenerateSshKeySheet } from "./GenerateSshKeySheet";
import {
  ADD_CLIENT_ID,
  DEPLOY_KEYS,
  GENERATE_KEY,
  HOSTS,
  LONG_CONNECTIONS,
  LONG_KEYS,
  REVEAL,
} from "./settings-source-providers.fixtures";
import { DEPLOY_KEYS_LIST, SOURCE_HOSTS_LIST } from "./source-providers-list";
import {
  DeployKeysView,
  type DeployKeysViewProps,
  SourceHostsView,
  type SourceHostsViewProps,
  SourceProvidersScreen,
  WebhookSecretReveal,
} from "./SourceProvidersScreen";

const meta: Meta = {
  title: "Screens/Settings/SourceProviders/SourceProvidersScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

/**
 * The slots the route fills with containers. The connect sheets belong to
 * another group's files under app/, so stories leave them empty; the two
 * sheets from this folder render their views.
 */
const hostSlots = {
  renderConnectGithub: () => null,
  renderConnectGitlab: () => null,
  renderConnectSource: () => null,
  renderAddClientId: (connection: AstroliftSourceConnection | null, onClose: () => void) => (
    <AddClientIdSheet {...ADD_CLIENT_ID} connection={connection} onClose={onClose} />
  ),
};

const renderGenerateKey = (open: boolean, onOpenChange: (next: boolean) => void) => (
  <GenerateSshKeySheet {...GENERATE_KEY} open={open} onOpenChange={onOpenChange} />
);

type Initial = { initial?: Partial<ListState> };

function Hosts({ initial, ...over }: Partial<Omit<SourceHostsViewProps, "list">> & Initial) {
  const list = useLocalListState(SOURCE_HOSTS_LIST, initial);
  return <SourceHostsView {...HOSTS} {...hostSlots} {...over} list={list} />;
}

function Keys({ initial, ...over }: Partial<Omit<DeployKeysViewProps, "list">> & Initial) {
  const list = useLocalListState(DEPLOY_KEYS_LIST, initial);
  return (
    <DeployKeysView {...DEPLOY_KEYS} renderGenerateKey={renderGenerateKey} {...over} list={list} />
  );
}

function Screen({
  section = null,
  hosts = <Hosts />,
  keys = <Keys />,
}: {
  section?: string | null;
  hosts?: React.ReactNode;
  keys?: React.ReactNode;
}) {
  return (
    <SourceProvidersScreen section={useLocalSettingsSection(section)} hosts={hosts} keys={keys} />
  );
}

/** Hosts, the default section. */
export const Full: Story = { render: () => <Screen /> };

/** SSH deploy keys: only that section is mounted. */
export const KeysSection: Story = { render: () => <Screen section="keys" /> };

export const Loading: Story = {
  render: () => <Screen hosts={<Hosts rows={[]} loading incompleteClientIdConnections={[]} />} />,
};

export const KeysLoading: Story = {
  render: () => <Screen section="keys" keys={<Keys rows={[]} loading />} />,
};

export const Empty: Story = {
  render: () => (
    <Screen hosts={<Hosts rows={[]} totalCount={0} incompleteClientIdConnections={[]} />} />
  ),
};

export const KeysEmpty: Story = {
  render: () => <Screen section="keys" keys={<Keys rows={[]} totalCount={0} />} />,
};

/** A search that matches no connection. */
export const EmptyFiltered: Story = {
  render: () => <Screen hosts={<Hosts rows={[]} initial={{ q: "bitbucket" }} />} />,
};

/** The page query failed: the list shows its retry state. */
export const LoadFailed: Story = {
  render: () => (
    <Screen
      hosts={
        <Hosts
          rows={[]}
          incompleteClientIdConnections={[]}
          error={{ message: "Network error: failed to fetch" }}
        />
      }
    />
  ),
};

export const KeysLoadFailed: Story = {
  render: () => (
    <Screen
      section="keys"
      keys={<Keys rows={[]} error={{ message: "Network error: failed to fetch" }} />}
    />
  ),
};

/** Mine: your personal connections, with the note saying how. */
export const MineHosts: Story = {
  render: () => (
    <Screen
      hosts={
        <Hosts
          rows={HOSTS.rows.filter((c) => c.isPersonal)}
          initial={{ view: "mine" }}
          incompleteClientIdConnections={[]}
        />
      }
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <Screen
      hosts={
        <Hosts
          rows={LONG_CONNECTIONS}
          totalCount={LONG_CONNECTIONS.length}
          incompleteClientIdConnections={LONG_CONNECTIONS.filter((c) => c.needsClientId)}
        />
      }
    />
  ),
};

export const LongKeys: Story = {
  render: () => <Screen section="keys" keys={<Keys rows={LONG_KEYS} nextCursor="c2" />} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <Screen hosts={<Hosts rows={LONG_CONNECTIONS} />} />
    </div>
  ),
};

/** The one-time reveal shown after a webhook secret rotation. */
export const SecretRevealed: Story = {
  render: () => <WebhookSecretReveal reveal={REVEAL} onClose={() => {}} />,
};

function SecretLauncher() {
  const [open, setOpen] = React.useState(false);
  return (
    <>
      <Button onClick={() => setOpen(true)}>Show rotated secret</Button>
      {open && <WebhookSecretReveal reveal={REVEAL} onClose={() => setOpen(false)} />}
    </>
  );
}

export const SecretKeyboard: Story = {
  render: () => <SecretLauncher />,
  play: async ({ canvasElement }) => {
    const opener = within(canvasElement).getByRole("button", { name: "Show rotated secret" });
    opener.focus();
    await userEvent.keyboard("{Enter}");
    const dialog = within(document.body).getByRole("dialog", { name: "Webhook secret generated" });
    await expect(within(dialog).getByRole("button", { name: "Copy URL" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    await expect(opener).toHaveFocus();
  },
};
