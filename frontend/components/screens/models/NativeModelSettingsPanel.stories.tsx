import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import {
  NativeModelSettingsPanel,
  type NativeModelSettingsPanelProps,
} from "./NativeModelSettingsPanel";
import { fakeModelPage } from "./shared-model.fixtures";
const noop = () => {};
export const nativeSettingsProps: NativeModelSettingsPanelProps = {
  allowed: true,
  loading: false,
  reason: null,
  onRetry: noop,
  name: "Native text",
  onName: noop,
  subscriptions: true,
  onSubscriptions: noop,
  mode: "SHARED",
  onMode: noop,
  app: null,
  apps: fakeModelPage(),
  onApp: noop,
  canUpdate: true,
  busy: false,
  sent: false,
  outcome: null,
  error: null,
  review: null,
  canConfirm: true,
  onReview: noop,
  onDismiss: noop,
  onConfirm: async () => true,
};
const meta = {
  title: "Screens/Models/NativeModelSettingsPanel",
  component: NativeModelSettingsPanel,
  parameters: { layout: "padded" },
  args: nativeSettingsProps,
} satisfies Meta<typeof NativeModelSettingsPanel>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Available: Story = {};
export const ReadOnly: Story = {
  args: { allowed: false, reason: "Current actor cannot configure native connections." },
};
export const ReviewSettings: Story = { args: { review: "update" } };
export const ReviewRemoval: Story = { args: { review: "remove" } };
export const Queued: Story = { args: { sent: true, outcome: "queued" } };
export const Removed: Story = { args: { sent: true, outcome: "removed" } };
export const Uncertain: Story = {
  args: { sent: true, error: "Current write outcome is unconfirmed." },
};
