import type { Meta, StoryObj } from "@storybook/nextjs-vite";
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
