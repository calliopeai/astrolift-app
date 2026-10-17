import { MockedProvider } from "@apollo/client/testing/react";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { GET_BRAIN_STATUS } from "@/graphql/registry/brains.queries";

import { BEHIND, HEALTHY, statusData } from "./brain-status.fixtures";
import { BrainRowStatus } from "./BrainRowStatus";

/**
 * A list row's status columns with their own read: the three columns of a
 * row share one `astroliftBrainStatus` request, in the environment the row
 * is served in (production when it has one).
 */
const meta: Meta = {
  title: "Screens/Brains/BrainRowStatus",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

function Row({ slug, environments }: { slug: string; environments: { name: string }[] }) {
  return (
    <div className="flex gap-8">
      <BrainRowStatus slug={slug} environments={environments} part="health" />
      <BrainRowStatus slug={slug} environments={environments} part="rebuilt" />
      <BrainRowStatus slug={slug} environments={environments} part="freshness" />
    </div>
  );
}

/** Read in production, the environment the layer is served in. */
export const Answered: Story = {
  render: () => (
    <MockedProvider
      mocks={[
        {
          request: {
            query: GET_BRAIN_STATUS,
            variables: { slug: "platform-brain", environmentName: "production" },
          },
          result: { data: statusData(HEALTHY) },
          maxUsageCount: 3,
        },
      ]}
    >
      <Row slug="platform-brain" environments={[{ name: "production" }]} />
    </MockedProvider>
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText("4f1c2a9")).toBeInTheDocument();
  },
};

/** A layer served only in staging is read in staging. */
export const OtherEnvironment: Story = {
  render: () => (
    <MockedProvider
      mocks={[
        {
          request: {
            query: GET_BRAIN_STATUS,
            variables: { slug: "sales-brain", environmentName: "staging" },
          },
          result: { data: statusData({ ...BEHIND, environmentName: "staging" }) },
          maxUsageCount: 3,
        },
      ]}
    >
      <Row slug="sales-brain" environments={[{ name: "staging" }]} />
    </MockedProvider>
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText("Behind head")).toBeInTheDocument();
  },
};

/** The read failed: Health says so, the other columns say nothing they cannot know. */
export const ReadFailed: Story = {
  render: () => (
    <MockedProvider
      mocks={[
        {
          request: {
            query: GET_BRAIN_STATUS,
            variables: { slug: "ops-brain", environmentName: "production" },
          },
          error: new Error("source head: the host answered 502"),
          maxUsageCount: 3,
        },
      ]}
    >
      <Row slug="ops-brain" environments={[]} />
    </MockedProvider>
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(await canvas.findByText("Unavailable")).toBeInTheDocument();
  },
};

/** Still in flight. */
export const Loading: Story = {
  render: () => (
    <MockedProvider
      mocks={[
        {
          request: {
            query: GET_BRAIN_STATUS,
            variables: { slug: "slow-brain", environmentName: "production" },
          },
          result: { data: statusData(HEALTHY) },
          delay: Infinity,
        },
      ]}
    >
      <Row slug="slow-brain" environments={[]} />
    </MockedProvider>
  ),
};
