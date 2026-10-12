import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ActionSafetyView } from "./ActionSafetyView";
import { ACTION_SAFETY_FIXTURE } from "./fixtures";

const meta: Meta<typeof ActionSafetyView> = {
  title: "Screens/Administration/Permissions/ActionSafety",
  component: ActionSafetyView,
  parameters: { layout: "fullscreen" },
  args: {
    rows: ACTION_SAFETY_FIXTURE,
    loading: false,
    error: null,
    onRetry: () => {},
    saving: null,
    saveError: null,
    onSetRule: async () => {},
  },
};
export default meta;
type Story = StoryObj<typeof meta>;

export const Gates: Story = {};
export const Saving: Story = { args: { saving: "secret.change" } };
export const SaveRefused: Story = {
  args: {
    saveError: "only the organization can turn a gate off; a narrower scope can only add to it",
  },
};
export const Loading: Story = { args: { rows: null, loading: true } };
export const ReadFailed: Story = { args: { rows: null, error: "Network error" } };
