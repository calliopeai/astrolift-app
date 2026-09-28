import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { fakeController } from "@/components/data-table/fixtures";
import type { AstroliftSourceConnection, AstroliftSshDeployKey } from "@/graphql/scm/scm.types";

import { AddClientIdSheet } from "./AddClientIdSheet";
import { GenerateSshKeySheet } from "./GenerateSshKeySheet";
import {
  ADD_CLIENT_ID,
  GENERATE_KEY,
  LONG_CONNECTIONS,
  LONG_KEYS,
  REVEAL,
  SCREEN,
} from "./settings-source-providers.fixtures";
import { SourceProvidersScreen, WebhookSecretReveal } from "./SourceProvidersScreen";

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
const slots = {
  renderConnectGithub: () => null,
  renderConnectGitlab: () => null,
  renderConnectSource: () => null,
  renderGenerateKey: (open: boolean, onOpenChange: (next: boolean) => void) => (
    <GenerateSshKeySheet {...GENERATE_KEY} open={open} onOpenChange={onOpenChange} />
  ),
  renderAddClientId: (connection: AstroliftSourceConnection | null, onClose: () => void) => (
    <AddClientIdSheet {...ADD_CLIENT_ID} connection={connection} onClose={onClose} />
  ),
};

export const Full: Story = {
  render: () => <SourceProvidersScreen {...SCREEN} {...slots} />,
};

export const Loading: Story = {
  render: () => (
    <SourceProvidersScreen
      {...SCREEN}
      {...slots}
      incompleteClientIdConnections={[]}
      connTable={fakeController<AstroliftSourceConnection>({ state: "loading" })}
      keyTable={fakeController<AstroliftSshDeployKey>({ state: "loading" })}
    />
  ),
};

export const Empty: Story = {
  render: () => (
    <SourceProvidersScreen
      {...SCREEN}
      {...slots}
      incompleteClientIdConnections={[]}
      connTable={fakeController<AstroliftSourceConnection>({ state: "empty", totalCount: 0 })}
      keyTable={fakeController<AstroliftSshDeployKey>({ state: "empty", totalCount: 0 })}
    />
  ),
};

/** A search that matches nothing in either table. */
export const EmptyFiltered: Story = {
  render: () => (
    <SourceProvidersScreen
      {...SCREEN}
      {...slots}
      connTable={fakeController<AstroliftSourceConnection>({
        state: "emptyFiltered",
        search: "bitbucket",
        isFiltered: true,
        totalCount: 0,
      })}
      keyTable={fakeController<AstroliftSshDeployKey>({
        state: "emptyFiltered",
        search: "nope",
        isFiltered: true,
        totalCount: 0,
      })}
    />
  ),
};

/** Both page queries failed: each table shows its retry state. */
export const LoadFailed: Story = {
  render: () => (
    <SourceProvidersScreen
      {...SCREEN}
      {...slots}
      incompleteClientIdConnections={[]}
      connTable={fakeController<AstroliftSourceConnection>({
        state: "error",
        error: new Error("Network error: failed to fetch"),
      })}
      keyTable={fakeController<AstroliftSshDeployKey>({
        state: "error",
        error: new Error("Network error: failed to fetch"),
      })}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <SourceProvidersScreen
      {...SCREEN}
      {...slots}
      incompleteClientIdConnections={LONG_CONNECTIONS.filter((c) => c.needsClientId)}
      connTable={fakeController<AstroliftSourceConnection>({
        rows: LONG_CONNECTIONS,
        totalCount: LONG_CONNECTIONS.length,
        sortEnabled: false,
      })}
      keyTable={fakeController<AstroliftSshDeployKey>({
        rows: LONG_KEYS,
        totalCount: LONG_KEYS.length,
        sortEnabled: false,
      })}
    />
  ),
};

/** The one-time reveal shown after a webhook secret rotation. */
export const SecretRevealed: Story = {
  render: () => <WebhookSecretReveal reveal={REVEAL} onClose={() => {}} />,
};
