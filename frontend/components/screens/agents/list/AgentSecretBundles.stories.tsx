import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ATTACHMENT, BUNDLES, BUNDLES_LIST, LONG_BUNDLE } from "./agents-dispatch-secrets.fixtures";
import { AgentSecretBundlesView } from "./AgentSecretBundles";

const meta: Meta = {
  title: "Screens/Agents/List/AgentSecretBundles",
};
export default meta;

type Story = StoryObj;

/** One bundle attached (with its prefix and position), one not. */
export const Full: Story = {
  render: () => <AgentSecretBundlesView {...BUNDLES} />,
};

export const Loading: Story = {
  render: () => (
    <AgentSecretBundlesView {...BUNDLES} bundles={[]} defaultAttachments={[]} loading />
  ),
};

export const Empty: Story = {
  render: () => <AgentSecretBundlesView {...BUNDLES} bundles={[]} defaultAttachments={[]} />,
};

/**
 * The card has no error state: failed queries render as Empty and failed
 * writes are toasts. Closest real state: a key being revealed while busy.
 */
export const Revealed: Story = {
  render: () => (
    <AgentSecretBundlesView
      {...BUNDLES}
      reveals={{ "bundle-1:GITHUB_APP_ID": "123456" }}
      busy="key-reveal:bundle-1:GITHUB_PRIVATE_KEY"
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <AgentSecretBundlesView
      {...BUNDLES}
      bundles={[LONG_BUNDLE, ...BUNDLES_LIST]}
      defaultAttachments={[
        {
          ...ATTACHMENT,
          id: "attach-long",
          bundleId: LONG_BUNDLE.id,
          bundleName: LONG_BUNDLE.name,
          prefix: "A_VERY_LONG_PREFIX_",
        },
      ]}
    />
  ),
};
