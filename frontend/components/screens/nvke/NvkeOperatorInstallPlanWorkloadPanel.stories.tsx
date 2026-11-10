import { NextIntlClientProvider, type AbstractIntlMessages } from "next-intl";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import de from "@/messages/de.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import pt from "@/messages/pt-BR.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import { INSTALL_TREE_PROPS } from "./NvkeOperatorInstallPlanTreePanel.stories";
import {
  NvkeOperatorInstallPlanWorkloadPanel,
  type NvkeOperatorInstallPlanWorkloadPanelProps,
} from "./NvkeOperatorInstallPlanWorkloadPanel";
const tree = INSTALL_TREE_PROPS.artifacts!;
export const INSTALL_WORKLOAD_PROPS: NvkeOperatorInstallPlanWorkloadPanelProps = {
  ...INSTALL_TREE_PROPS,
  scopeKey: "storybook-install-workload",
  tree,
  artifacts: {
    id: "c098fc8d-3f48-45fd-b9b0-7d670261e9ef",
    version: 2,
    status: "prepared",
    planId: tree.planId,
    planVersion: tree.planVersion,
    planDigest: tree.planDigest,
    chartArtifactId: tree.chartArtifactId,
    chartArtifactVersion: tree.chartArtifactVersion,
    chartArtifactDigest: tree.chartArtifactDigest,
    treeId: tree.id,
    treeVersion: tree.version,
    treeDigest: tree.treeDigest,
    operationId: tree.operationId,
    operationVersion: tree.operationVersion,
    siteId: tree.siteId,
    siteVersion: tree.siteVersion,
    authorityId: tree.authorityId,
    authorityVersion: tree.authorityVersion,
    artifactDigest: "1".repeat(64),
    sourceDigest: "2".repeat(64),
    captureDigest: "3".repeat(64),
    workloadDigest: "4".repeat(64),
    itemCount: INSTALL_TREE_PROPS.plan!.itemCount,
    resourceCount: 2,
    imageUseCount: 3,
    imageReferenceCount: 1,
    issueCount: 0,
    observedAt: "2026-11-10T00:00:00+00:00",
    continuationAuthorized: false,
    executionState:
      "requires-reviewed-operator-images-platform-credentials-rewrite-import-use-and-linked-admission",
  },
  retainedArtifactId: null,
  canRetry: true,
  onPrepare: async () => true,
  onReview: async () => true,
  onRetryPreparation: async () => true,
};
export default {
  title: "Screens/NVKE/Install workload inventory",
  component: NvkeOperatorInstallPlanWorkloadPanel,
  excludeStories: ["INSTALL_WORKLOAD_PROPS"],
  args: INSTALL_WORKLOAD_PROPS,
} satisfies Meta<typeof NvkeOperatorInstallPlanWorkloadPanel>;
type Story = StoryObj<typeof NvkeOperatorInstallPlanWorkloadPanel>;
export const Full: Story = {};
export const Unloaded: Story = { args: { artifacts: null } };
export const ParentRequired: Story = { args: { artifacts: null, tree: null } };
export const Loading: Story = { args: { artifacts: null, loading: true } };
export const Pending: Story = { args: { pending: true } };
export const LostReply: Story = {
  args: {
    artifacts: null,
    retainedArtifactId: INSTALL_WORKLOAD_PROPS.artifacts!.id,
    error: "UNCONFIRMED",
  },
};
export const OtherReviewer: Story = { args: { canRetry: false } };
export const ReadOnly: Story = { args: { canReview: false } };
export const StaleParent: Story = {
  args: { plan: { ...INSTALL_TREE_PROPS.plan!, version: INSTALL_TREE_PROPS.plan!.version + 1 } },
};
export const StaleTree: Story = { args: { tree: { ...tree, version: tree.version + 1 } } };
export const StaleSource: Story = {
  args: {
    operation: {
      ...INSTALL_TREE_PROPS.operation,
      version: INSTALL_TREE_PROPS.operation.version + 1,
    },
  },
};
export const Unverified: Story = {
  args: { authority: { ...INSTALL_TREE_PROPS.authority!, verificationCode: "UNVERIFIED" } },
};
export const OriginalPreparationRecovery: Story = {
  args: {
    artifacts: null,
    retainedArtifactId: INSTALL_WORKLOAD_PROPS.artifacts!.id,
    allowOriginalPreparation: true,
  },
};
export const NoImageUses: Story = {
  args: {
    artifacts: { ...INSTALL_WORKLOAD_PROPS.artifacts!, imageUseCount: 0, imageReferenceCount: 0 },
  },
};
export const JoiningIssues: Story = {
  args: { artifacts: { ...INSTALL_WORKLOAD_PROPS.artifacts!, issueCount: 2 } },
};
export const Narrow: Story = { parameters: { viewport: { defaultViewport: "tablet" } } };

function localized(
  locale: string,
  messages: AbstractIntlMessages,
  args: NvkeOperatorInstallPlanWorkloadPanelProps
) {
  return (
    <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
      <NvkeOperatorInstallPlanWorkloadPanel {...args} />
    </NextIntlClientProvider>
  );
}
export const German: Story = { render: (args) => localized("de", de, args) };
export const Spanish: Story = { render: (args) => localized("es", es, args) };
export const French: Story = { render: (args) => localized("fr", fr, args) };
export const Portuguese: Story = { render: (args) => localized("pt-BR", pt, args) };
export const Japanese: Story = { render: (args) => localized("ja", ja, args) };
export const Korean: Story = { render: (args) => localized("ko", ko, args) };
export const Chinese: Story = { render: (args) => localized("zh-Hans", zh, args) };

export const ChooseAnotherArtifact: Story = { args: { onChooseArtifact: async () => {} } };
