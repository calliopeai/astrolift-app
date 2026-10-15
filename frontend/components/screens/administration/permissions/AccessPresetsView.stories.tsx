import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AccessPresetsView } from "./AccessPresetsView";
import { DeployApprovalSetup } from "./DeployApprovalSetup";
import { ACCESS_PRESETS_FIXTURE, DEPLOY_APPROVAL_FIXTURE } from "./fixtures";

const meta: Meta<typeof AccessPresetsView> = {
  title: "Screens/Administration/Permissions/AccessPresets",
  component: AccessPresetsView,
  parameters: { layout: "fullscreen" },
  args: { presets: ACCESS_PRESETS_FIXTURE, loading: false, error: null, onRetry: () => {} },
};
export default meta;
type Story = StoryObj<typeof meta>;

export const Presets: Story = {};
/** The whole page: presets, guided deploy approval and the way to the model-connection policy. */
export const WithDeployApproval: Story = {
  args: { deployApproval: <DeployApprovalSetup {...DEPLOY_APPROVAL_FIXTURE} /> },
};
export const Loading: Story = { args: { presets: null, loading: true } };
export const ReadFailed: Story = { args: { presets: null, error: "Network error" } };
