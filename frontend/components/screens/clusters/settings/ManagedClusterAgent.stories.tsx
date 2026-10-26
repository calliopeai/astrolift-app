import { MockedProvider } from "@apollo/client/testing/react";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";
import {
  GET_CLUSTER_AGENT_INSTALL,
  INSTALL_CLUSTER_AGENT,
  REVIEW_CLUSTER_AGENT_INSTALL,
} from "@/graphql/clusters/clusters.queries";
import messages from "@/messages/en.json";
import { ManagedClusterAgent } from "./ManagedClusterAgent";

const meta: Meta = { title: "Screens/Clusters/Settings/ManagedClusterAgent" };
export default meta;
type Story = StoryObj;

function fixture(status: "SUCCEEDED" | "AWAITING_HEARTBEAT" | "UNCERTAIN") {
  const row = {
    id: "installation-demo",
    clusterId: "cluster-demo",
    requestId: "request-demo",
    status,
    secretConfirmed: true,
    deploymentConfirmed: true,
    heartbeatConfirmed: status === "SUCCEEDED",
    retryable: status === "UNCERTAIN",
    errorCode: status === "UNCERTAIN" ? "PROVIDER_UNCERTAIN" : "",
    errorMessage:
      status === "UNCERTAIN" ? "Retry the same installation to confirm its outcome." : "",
  };
  return (
    <MockedProvider
      mocks={[
        {
          request: {
            query: REVIEW_CLUSTER_AGENT_INSTALL,
            variables: { clusterId: "cluster-demo" },
          },
          result: {
            data: {
              astroliftClusterAgentInstallReview: {
                clusterId: "cluster-demo",
                version: 1,
                source: "review-demo",
              },
            },
          },
        },
        {
          request: { query: INSTALL_CLUSTER_AGENT, variables: () => true },
          result: { data: { installClusterAgent: { ok: true, errors: [], data: row } } },
        },
        {
          request: { query: GET_CLUSTER_AGENT_INSTALL, variables: { installId: row.id } },
          maxUsageCount: Number.POSITIVE_INFINITY,
          result: { data: { astroliftClusterAgentInstall: row } },
        },
      ]}
    >
      <ManagedClusterAgent clusterId="cluster-demo" />
    </MockedProvider>
  );
}

export const Ready: Story = { render: () => fixture("SUCCEEDED") };

export const Connected: Story = {
  render: () => fixture("SUCCEEDED"),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(
      canvas.getByRole("button", { name: messages.clusterSettings.agent.deploy })
    );
    await expect(
      await canvas.findByText(messages.clusterSettings.agent.managedConnected)
    ).toBeVisible();
  },
};

export const AwaitingHeartbeat: Story = {
  render: () => fixture("AWAITING_HEARTBEAT"),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(
      canvas.getByRole("button", { name: messages.clusterSettings.agent.deploy })
    );
    await expect(
      await canvas.findByText(messages.clusterSettings.agent.managedPending)
    ).toBeVisible();
    await expect(canvas.getByRole("button")).toBeDisabled();
  },
};

export const Retryable: Story = {
  render: () => fixture("UNCERTAIN"),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(
      canvas.getByRole("button", { name: messages.clusterSettings.agent.deploy })
    );
    await expect(await canvas.findByRole("alert")).toHaveTextContent("Retry the same installation");
  },
};
