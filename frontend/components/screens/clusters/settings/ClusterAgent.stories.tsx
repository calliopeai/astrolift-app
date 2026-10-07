import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";
import { NextIntlClientProvider } from "next-intl";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";

import { ClusterAgentView } from "./ClusterAgent";
import { AGENT, AGENT_ISSUED, LONG } from "./fixtures";

const meta: Meta = { title: "Screens/Clusters/Settings/ClusterAgent" };
export default meta;

type Story = StoryObj;

export const Provisioned: Story = { render: () => <ClusterAgentView {...AGENT} /> };

/** No key issued yet. */
export const Empty: Story = {
  render: () => <ClusterAgentView {...AGENT} provisioned={false} />,
};

/** The one time the raw key is shown. */
export const KeyIssued: Story = { render: () => <ClusterAgentView {...AGENT_ISSUED} /> };

const CONTROLLERS_WITHHELD =
  "Cluster-wide changes are withheld from Astrolift on this install: on AWS clusters it holds the minimal Kubernetes RBAC in deploy/rbac/control-plane-minimal.yaml, not cluster admin. Controllers, CRDs, cluster roles, storage classes and persistent volumes are the cluster owner's to install.";

/** calliope-installer#447: the agent's ClusterRole is the owner's; deploy is offered disabled, with the reason. */
export const DeployWithheld: Story = {
  render: () => <ClusterAgentView {...AGENT} deployWithheldReason={CONTROLLERS_WITHHELD} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText(/Cluster-wide changes are withheld/)).toBeVisible();
    const deploy = canvas.getByRole("button", { name: /deploy/i });
    await expect(deploy).toBeDisabled();
    await expect(deploy).toHaveAttribute("aria-describedby", "agent-deploy-withheld");
    await expect(canvas.getByRole("button", { name: /rotate/i })).toBeEnabled();
  },
};

export const Loading: Story = {
  render: () => <ClusterAgentView {...AGENT} issuing deploying />,
};

export const GermanIssued: Story = {
  render: () => (
    <NextIntlClientProvider locale="de" messages={de}>
      <ClusterAgentView {...AGENT_ISSUED} />
    </NextIntlClientProvider>
  ),
};

export const JapaneseUnprovisioned: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja}>
      <ClusterAgentView {...AGENT} provisioned={false} />
    </NextIntlClientProvider>
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <ClusterAgentView {...AGENT_ISSUED} />
    </div>
  ),
};

export const LongStrings: Story = {
  render: () => (
    <ClusterAgentView
      {...AGENT_ISSUED}
      issued={{
        ...AGENT_ISSUED.issued!,
        agentKey: `astk_${LONG}`,
        heartbeatUrl: `https://${LONG}.example.com/api/clusters/v1/${LONG}/heartbeat/`,
      }}
    />
  ),
};
