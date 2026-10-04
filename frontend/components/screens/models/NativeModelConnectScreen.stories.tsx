import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";
import {
  NativeModelConnectScreen,
  type NativeModelConnectScreenProps,
} from "./NativeModelConnectScreen";
import { nativeModel, nativeSource } from "./native-model.fixtures";
import { fakeModelPage } from "./shared-model.fixtures";

const noop = () => {};
const run = async () => {};
export const nativeConnectProps: NativeModelConnectScreenProps = {
  step: 1,
  allowed: true,
  supportLoading: false,
  supportReason: null,
  onRetrySupport: noop,
  cluster: null,
  clusters: fakeModelPage({
    rows: [
      {
        id: nativeModel.clusterId,
        version: 3,
        providerId: nativeModel.providerId,
        providerVersion: 2,
        name: "Research",
        slug: "research",
        region: "us-east-1",
      },
    ],
  }),
  onSelectCluster: run,
  kind: "FOUNDATION_MODEL",
  onKind: noop,
  lookup: "",
  onLookup: noop,
  catalogue: null,
  detail: null,
  busy: false,
  error: null,
  sent: false,
  registeredId: null,
  onLoad: run,
  onInspect: run,
  name: "Native text",
  onName: noop,
  subscriptions: true,
  onSubscriptions: noop,
  mode: "SHARED",
  onMode: noop,
  app: null,
  apps: fakeModelPage(),
  onApp: noop,
  review: null,
  canReview: false,
  onReview: run,
  onRegister: run,
  onBack: noop,
};
const sourceProps = {
  ...nativeConnectProps,
  step: 2 as const,
  cluster: nativeConnectProps.clusters.rows[0],
  detail: null,
  canReview: true,
  catalogue: {
    items: [nativeSource],
    state: "metadata",
    reason: null,
    partial: false,
    truncated: false,
  },
};
const reviewProps = {
  ...sourceProps,
  step: 4 as const,
  detail: nativeSource,
  review: {
    organizationId: nativeModel.organizationId,
    clusterId: nativeModel.clusterId,
    expectedProviderId: nativeModel.providerId,
    expectedClusterVersion: 3,
    expectedProviderVersion: 2,
    sourceKind: "FOUNDATION_MODEL" as const,
    sourceIdentifier: nativeSource.identity.sourceId,
    sourceFingerprint: nativeSource.identity.sourceFingerprint,
    name: "Native text",
    allowSubscriptions: true,
    sharingMode: "SHARED" as const,
    dedicatedAppId: null,
    ifMatchDedicatedAppVersion: null,
  },
};
const meta = {
  title: "Screens/Models/NativeModelConnectScreen",
  component: NativeModelConnectScreen,
  parameters: { layout: "fullscreen" },
  args: nativeConnectProps,
} satisfies Meta<typeof NativeModelConnectScreen>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Placement: Story = {};
export const Source: Story = { args: sourceProps };
export const Settings: Story = {
  args: { ...sourceProps, step: 3, detail: nativeSource },
};
export const DedicatedSettings: Story = {
  args: { ...sourceProps, step: 3, detail: nativeSource, mode: "DEDICATED", canReview: false },
};
export const BoundedPartial: Story = {
  args: {
    ...sourceProps,
    catalogue: {
      ...sourceProps.catalogue,
      partial: true,
      truncated: true,
      reason: "Some source metadata could not be verified.",
    },
  },
};
export const Review: Story = { args: reviewProps };
export const Registered: Story = {
  args: { ...reviewProps, sent: true, registeredId: nativeModel.id },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByText("Connection registered. Invoke access remains unverified.")
    ).toBeVisible();
    await expect(canvas.getByRole("link", { name: "Open deployment" })).toHaveAttribute(
      "href",
      `/models/shared/${nativeModel.id}`
    );
  },
};
export const UnknownWrite: Story = {
  args: { ...reviewProps, sent: true, error: "Registration reply was not confirmed." },
};
export const Disabled: Story = {
  args: { allowed: false, supportReason: "Native connections are disabled by the operator." },
};
