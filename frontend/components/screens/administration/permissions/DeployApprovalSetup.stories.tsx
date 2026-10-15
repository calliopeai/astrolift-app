import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DeployApprovalSetup } from "./DeployApprovalSetup";
import { DEPLOY_APPROVAL_FIXTURE } from "./fixtures";

/**
 * Easy setup › Require approval before deploys (#2272): writes the app's own
 * deploy-approval setting, which applies to every deploy of that app.
 */
const meta: Meta<typeof DeployApprovalSetup> = {
  title: "Screens/Administration/Permissions/DeployApprovalSetup",
  component: DeployApprovalSetup,
  parameters: { layout: "padded" },
  args: DEPLOY_APPROVAL_FIXTURE,
};
export default meta;
type Story = StoryObj<typeof meta>;

/** Nothing picked yet. */
export const PickAnApp: Story = {};
/** An app with approval off: turning it on names a team and a count. */
export const ApprovalOff: Story = { args: { initialAppId: "app-checkout" } };
/** An app already gated by a team. */
export const ApprovalOn: Story = { args: { initialAppId: "app-billing" } };
/** Without app.update on the app the setting is shown, not changeable. */
export const ReadOnly: Story = { args: { initialAppId: "app-ledger" } };
export const SaveFailed: Story = {
  args: {
    initialAppId: "app-checkout",
    onSave: async () => "The app changed since it was read; reload and try again.",
  },
};
export const NoApps: Story = { args: { apps: [] } };
export const Loading: Story = { args: { apps: null, loading: true } };
export const ReadFailed: Story = { args: { apps: null, error: "Network error" } };
