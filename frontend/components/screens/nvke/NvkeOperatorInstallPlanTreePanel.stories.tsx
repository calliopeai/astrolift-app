import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import {
  NvkeOperatorInstallPlanTreePanel,
  type NvkeOperatorInstallPlanTreePanelProps,
} from "./NvkeOperatorInstallPlanTreePanel";
import { INSTALL_CHARTS_PROPS } from "./NvkeOperatorInstallPlanChartsPanel.stories";
import { INSTALL_PLAN_PROPS } from "./NvkeOperatorInstallPlanPanel.stories";

const parent = INSTALL_PLAN_PROPS.plan!;
export const INSTALL_TREE_PROPS: NvkeOperatorInstallPlanTreePanelProps = {
  scopeKey: "storybook-install-tree",
  site: INSTALL_PLAN_PROPS.site,
  operation: INSTALL_PLAN_PROPS.operation,
  authority: INSTALL_PLAN_PROPS.authority,
  plan: parent,
  charts: INSTALL_CHARTS_PROPS.artifacts,
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
    id: "87a76c80-3468-43aa-b2be-e511d587d312",
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
    chartArtifactId: INSTALL_CHARTS_PROPS.artifacts!.id,
    chartArtifactVersion: INSTALL_CHARTS_PROPS.artifacts!.version,
    chartArtifactDigest: INSTALL_CHARTS_PROPS.artifacts!.artifactDigest,
    treeDigest: "d".repeat(64),
    fileCount: 3,
    bytes: "9007199254740994",
    executionState: "requires-native-rendering-images-and-live-verification",
    continuationAuthorized: false,
  },
};
export default {
  title: "Screens/NVKE/Install plan tree",
  component: NvkeOperatorInstallPlanTreePanel,
  excludeStories: ["INSTALL_TREE_PROPS"],
  args: INSTALL_TREE_PROPS,
} satisfies Meta<typeof NvkeOperatorInstallPlanTreePanel>;
type Story = StoryObj<typeof NvkeOperatorInstallPlanTreePanel>;
export const Full: Story = {};
export const Unloaded: Story = { args: { artifacts: null } };
export const ParentRequired: Story = { args: { plan: null, artifacts: null } };
export const Loading: Story = { args: { artifacts: null, loading: true } };
export const Pending: Story = { args: { pending: true } };
export const LostReply: Story = {
  args: {
    artifacts: null,
    retainedArtifactId: INSTALL_TREE_PROPS.artifacts!.id,
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
export const ChartsRequired: Story = { args: { charts: null, artifacts: null } };
export const StaleCharts: Story = {
  args: { charts: { ...INSTALL_CHARTS_PROPS.artifacts!, version: 3 } },
};
export const OriginalPreparationRecovery: Story = {
  args: {
    artifacts: null,
    retainedArtifactId: INSTALL_TREE_PROPS.artifacts!.id,
    allowOriginalPreparation: true,
    error: "UNCONFIRMED",
  },
};
export const ChooseAnotherArtifact: Story = { args: { onChooseArtifact: async () => {} } };
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768 }}>
      <NvkeOperatorInstallPlanTreePanel {...args} />
    </div>
  ),
};
