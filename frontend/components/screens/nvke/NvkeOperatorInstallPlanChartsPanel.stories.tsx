import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import {
  NvkeOperatorInstallPlanChartsPanel,
  type NvkeOperatorInstallPlanChartsPanelProps,
} from "./NvkeOperatorInstallPlanChartsPanel";
import { INSTALL_PLAN_PROPS } from "./NvkeOperatorInstallPlanPanel.stories";

const parent = INSTALL_PLAN_PROPS.plan!;
export const INSTALL_CHARTS_PROPS: NvkeOperatorInstallPlanChartsPanelProps = {
  scopeKey: "storybook-install-charts",
  site: INSTALL_PLAN_PROPS.site,
  operation: INSTALL_PLAN_PROPS.operation,
  authority: INSTALL_PLAN_PROPS.authority,
  plan: parent,
  canReview: true,
  pending: false,
  loading: false,
  error: null,
  retainedArtifactId: null,
  canRetry: true,
  onPrepare: async () => true,
  onReview: async () => true,
  onRetryPreparation: async () => true,
  artifacts: {
    id: "123e4567-e89b-12d3-a456-426614174096",
    version: 2,
    status: "prepared",
    planId: parent.id,
    planVersion: parent.version,
    operationId: parent.operationId,
    operationVersion: parent.operationVersion,
    siteId: parent.siteId,
    siteVersion: parent.siteVersion,
    authorityId: parent.authorityId,
    authorityVersion: parent.authorityVersion,
    planDigest: parent.plan.planDigest,
    artifactDigest: "e".repeat(64),
    chartCount: 2,
    bytes: "9007199254740993",
    expandedBytes: "9223372036854775807",
    executionState: "requires-rendering-images-and-live-verification",
    continuationAuthorized: false,
  },
};
export default {
  title: "Screens/NVKE/Install plan charts",
  component: NvkeOperatorInstallPlanChartsPanel,
  excludeStories: ["INSTALL_CHARTS_PROPS"],
  args: INSTALL_CHARTS_PROPS,
} satisfies Meta<typeof NvkeOperatorInstallPlanChartsPanel>;
type Story = StoryObj<typeof NvkeOperatorInstallPlanChartsPanel>;
export const Full: Story = {};
export const Unloaded: Story = { args: { artifacts: null } };
export const ParentRequired: Story = { args: { plan: null, artifacts: null } };
export const Loading: Story = { args: { artifacts: null, loading: true } };
export const Pending: Story = { args: { pending: true } };
export const LostReply: Story = {
  args: {
    artifacts: null,
    retainedArtifactId: INSTALL_CHARTS_PROPS.artifacts!.id,
    error: "UNCONFIRMED",
  },
};
export const ReadOnly: Story = { args: { canReview: false } };
export const OtherReviewer: Story = { args: { canRetry: false } };
export const StaleParent: Story = { args: { plan: { ...parent, version: parent.version + 1 } } };
export const StaleSource: Story = {
  args: { operation: { ...INSTALL_PLAN_PROPS.operation, version: parent.operationVersion + 1 } },
};
export const Unverified: Story = {
  args: { authority: { ...INSTALL_PLAN_PROPS.authority!, verificationCode: "UNVERIFIED" } },
};
export const Empty: Story = {
  args: {
    artifacts: {
      ...INSTALL_CHARTS_PROPS.artifacts!,
      chartCount: 0,
      bytes: "0",
      expandedBytes: "0",
    },
  },
};
export const OriginalPreparationRecovery: Story = {
  args: {
    artifacts: null,
    retainedArtifactId: INSTALL_CHARTS_PROPS.artifacts!.id,
    allowOriginalPreparation: true,
    error: "UNCONFIRMED",
  },
};
export const ChooseAnotherArtifact: Story = { args: { onChooseArtifact: async () => {} } };
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeOperatorInstallPlanChartsPanel {...args} />
    </div>
  ),
};
