import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AccessPresetsView } from "./AccessPresetsView";
import { ACCESS_PRESETS_FIXTURE } from "./fixtures";

const meta: Meta<typeof AccessPresetsView> = {
  title: "Screens/Administration/Permissions/AccessPresets",
  component: AccessPresetsView,
  parameters: { layout: "fullscreen" },
  args: { presets: ACCESS_PRESETS_FIXTURE, loading: false, error: null, onRetry: () => {} },
};
export default meta;
type Story = StoryObj<typeof meta>;

export const Presets: Story = {};
export const Loading: Story = { args: { presets: null, loading: true } };
export const ReadFailed: Story = { args: { presets: null, error: "Network error" } };
