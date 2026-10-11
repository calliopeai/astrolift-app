import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import { expect, userEvent, within } from "storybook/test";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import { SharedModelDetailScreen } from "./SharedModelDetailScreen";
import { sharedModelDetailProps } from "./shared-model-detail.fixtures";
const meta = {
  title: "Screens/Models/SharedModelDetailScreen",
  component: SharedModelDetailScreen,
  parameters: { layout: "padded" },
} satisfies Meta<typeof SharedModelDetailScreen>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Full: Story = {
  args: sharedModelDetailProps,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("provider-one")).not.toBeVisible();
    await userEvent.click(canvas.getByText("Technical details"));
    await expect(canvas.getByText("provider-one")).toBeVisible();
    await expect(canvas.getByText("operation-model-123")).toBeVisible();
  },
};
export const Loading: Story = { args: { ...sharedModelDetailProps, model: null, loading: true } };
export const Missing: Story = { args: { ...sharedModelDetailProps, model: null } };
export const FailedRead: Story = {
  args: { ...sharedModelDetailProps, model: null, error: "Shared model read denied" },
};
export const Stale: Story = {
  args: { ...sharedModelDetailProps, stale: true, error: "Model refresh failed" },
};
export const Unconfirmed: Story = {
  args: {
    ...sharedModelDetailProps,
    model: {
      ...sharedModelDetailProps.model!,
      ready: false,
      readinessObservedAt: null,
      readinessGeneration: null,
      appliedResources: null,
      operationCompletedAt: null,
    },
  },
};
export const UnsupportedRuntime: Story = {
  args: {
    ...sharedModelDetailProps,
    model: {
      ...sharedModelDetailProps.model!,
      runtimeSupported: false,
      runtimeReason: "Pinned runtime admission is unavailable",
    },
  },
};
export const CPU: Story = {
  args: {
    ...sharedModelDetailProps,
    model: {
      ...sharedModelDetailProps.model!,
      computeMode: "cpu",
      desiredResources: {
        ...sharedModelDetailProps.model!.desiredResources,
        gpuCount: 0,
        cpuKvCacheGiB: 4,
      },
    },
  },
};
export const French: Story = {
  args: sharedModelDetailProps,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="fr" messages={fr} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
export const Japanese: Story = {
  args: sharedModelDetailProps,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="ja" messages={ja} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
export const Width768: Story = {
  args: {
    ...sharedModelDetailProps,
    model: {
      ...sharedModelDetailProps.model!,
      name: "Shared-generation-model-with-a-long-operation-name",
      modelRepo: "publisher/very-long-generation-model-repository-name-with-a-pinned-revision",
    },
  },
  render: (args) => (
    <div style={{ width: 768 }}>
      <SharedModelDetailScreen {...args} />
    </div>
  ),
};

export const LocalSourceSettings: Story = {
  args: {
    ...sharedModelDetailProps,
    model: {
      ...sharedModelDetailProps.model!,
      sourceKind: "local_artifact",
      localArtifactId: "00000000-0000-4000-8000-000000000099",
      localArtifactVersion: 2,
      localManifestSha256: "c".repeat(64),
      revisionSha: null,
      computeMode: "cpu",
    },
    management: <section id="model-settings">Local-source resource settings</section>,
  },
};
