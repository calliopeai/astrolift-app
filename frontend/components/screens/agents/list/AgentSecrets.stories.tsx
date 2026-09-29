import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import {
  BUNDLES,
  LONG_SECRET_ROW,
  SECRET_ROW_ERROR,
  SECRET_ROWS,
  SECRETS,
} from "./agents-dispatch-secrets.fixtures";
import { AgentSecretBundlesView } from "./AgentSecretBundles";
import { AgentSecretsView, type AgentSecretsViewProps } from "./AgentSecrets";

const meta: Meta = {
  title: "Screens/Agents/List/AgentSecrets",
};
export default meta;

type Story = StoryObj;

const bundles = <AgentSecretBundlesView {...BUNDLES} />;

/** Holds the open state the way the Dispatch tab does. */
function Controlled(props: AgentSecretsViewProps) {
  const [open, setOpen] = React.useState(true);
  return <AgentSecretsView {...props} open={open} onOpenChange={setOpen} />;
}

/** The dialog opened from the Dispatch tab's Advanced section. */
export const Dialog: Story = {
  render: () => <Controlled {...SECRETS} bundles={bundles} />,
};

/** Embedded on the agent Secrets route (no runtime settings). */
export const Embedded: Story = {
  render: () => <AgentSecretsView {...SECRETS} embedded bundles={bundles} />,
};

export const Loading: Story = {
  render: () => <AgentSecretsView {...SECRETS} embedded rows={[]} loading />,
};

export const Empty: Story = {
  render: () => <AgentSecretsView {...SECRETS} embedded rows={[]} />,
};

/** A ref whose status check failed shows the Error chip. */
export const StatusError: Story = {
  render: () => (
    <AgentSecretsView {...SECRETS} embedded rows={[SECRET_ROW_ERROR, ...SECRET_ROWS]} />
  ),
};

/** A value revealed through the audited Reveal action. */
export const Revealed: Story = {
  render: () => (
    <AgentSecretsView
      {...SECRETS}
      embedded
      reveals={{ ANTHROPIC_API_KEY: "sk-ant-api03-example-value-not-a-real-key" }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <AgentSecretsView
      {...SECRETS}
      embedded
      rows={[LONG_SECRET_ROW, ...SECRET_ROWS]}
      reveals={{ [LONG_SECRET_ROW.envVar]: `${"x".repeat(240)}` }}
    />
  ),
};
