import { NextIntlClientProvider } from "next-intl";
import de from "@/messages/de.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

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

/** Values disclosed by a successful audited provider read. */
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

export const PendingWrites: Story = {
  render: () => (
    <AgentSecretBundlesView
      {...BUNDLES}
      defaultAttachments={[]}
      pending={new Set(["attach:bundle-1", "key-delete:bundle-1:GITHUB_APP_ID"])}
    />
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const attach = canvas.getAllByRole("button", { name: "Attach" });
    await expect(attach[0]).toBeDisabled();
    await expect(attach[1]).toBeEnabled();
    await expect(canvas.getByRole("button", { name: "Delete GITHUB_APP_ID" })).toBeDisabled();
  },
};

export const ReadFailure: Story = {
  render: () => (
    <AgentSecretBundlesView
      {...BUNDLES}
      error={{ message: "ATTACHMENT_READ_REFUSED: the selected recipe could not be read" }}
    />
  ),
};

export const GermanReadFailureLongStrings: Story = {
  render: () => (
    <NextIntlClientProvider locale="de" timeZone="Europe/Berlin" messages={de}>
      <div style={{ width: 768 }}>
        <AgentSecretBundlesView
          {...BUNDLES}
          error={{ message: "PROVIDER_DIAGNOSTIC: " + "request-identifier/".repeat(30) }}
        />
      </div>
    </NextIntlClientProvider>
  ),
};
