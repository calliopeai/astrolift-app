import { INSTALL_WORKLOAD_PROPS } from "./NvkeOperatorInstallPlanWorkloadPanel.stories";
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
  NvkeOperatorInstallPlanImagePanel,
  type NvkeOperatorInstallPlanImagePanelProps,
} from "./NvkeOperatorInstallPlanImagePanel";
const tree = INSTALL_TREE_PROPS.artifacts!;
export const INSTALL_IMAGE_PROPS: NvkeOperatorInstallPlanImagePanelProps = {
  ...INSTALL_TREE_PROPS,
  scopeKey: "storybook-install-image",
  tree,
  artifacts: {
    id: "2be02f88-dc9b-47fb-9662-96504efde439",
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
    rootDigest: "sha256:" + "4".repeat(64),
    blobCount: 6,
    bytes: "67108864",
    observedAt: "2026-11-10T00:00:00+00:00",
    continuationAuthorized: false,
    executionState: "requires-workload-platform-credential-phase-use-and-linked-admission",
  },
  retainedArtifactId: null,
  canRetry: true,
  onPrepare: async () => true,
  onReview: async () => true,
  onRetryPreparation: async () => true,
};
export default {
  title: "Screens/NVKE/Install registry image",
  component: NvkeOperatorInstallPlanImagePanel,
  excludeStories: ["INSTALL_IMAGE_PROPS"],
  args: INSTALL_IMAGE_PROPS,
} satisfies Meta<typeof NvkeOperatorInstallPlanImagePanel>;
type Story = StoryObj<typeof NvkeOperatorInstallPlanImagePanel>;
export const Full: Story = {};
export const Unloaded: Story = { args: { artifacts: null } };
export const ParentRequired: Story = { args: { artifacts: null, tree: null } };
export const Loading: Story = { args: { artifacts: null, loading: true } };
export const Pending: Story = { args: { pending: true } };
export const LostReply: Story = {
  args: {
    artifacts: null,
    retainedArtifactId: INSTALL_IMAGE_PROPS.artifacts!.id,
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
    retainedArtifactId: INSTALL_IMAGE_PROPS.artifacts!.id,
    allowOriginalPreparation: true,
  },
};
export const LongDigest: Story = {
  args: {
    artifacts: { ...INSTALL_IMAGE_PROPS.artifacts!, rootDigest: "sha512:" + "4".repeat(128) },
  },
};
export const Narrow: Story = { parameters: { viewport: { defaultViewport: "tablet" } } };

function localized(
  locale: string,
  messages: AbstractIntlMessages,
  args: NvkeOperatorInstallPlanImagePanelProps
) {
  return (
    <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
      <NvkeOperatorInstallPlanImagePanel {...args} />
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

export const WorkloadBound: Story = {
  args: {
    ...INSTALL_IMAGE_PROPS,
    workload: INSTALL_WORKLOAD_PROPS.artifacts,
    artifacts: {
      ...INSTALL_IMAGE_PROPS.artifacts!,
      workloadId: INSTALL_WORKLOAD_PROPS.artifacts!.id,
      workloadVersion: INSTALL_WORKLOAD_PROPS.artifacts!.version,
      workloadDigest: INSTALL_WORKLOAD_PROPS.artifacts!.workloadDigest,
    },
  },
};
export const WorkloadRequired: Story = {
  args: { ...INSTALL_IMAGE_PROPS, workload: null, artifacts: null },
};
export const WorkloadUnloaded: Story = {
  args: {
    ...INSTALL_IMAGE_PROPS,
    workload: INSTALL_WORKLOAD_PROPS.artifacts,
    artifacts: null,
    retainedArtifactId: null,
  },
};

export const WorkloadPending: Story = { args: { ...WorkloadBound.args, pending: true } };
export const WorkloadReadOnly: Story = { args: { ...WorkloadBound.args, canReview: false } };
export const WorkloadStale: Story = {
  args: {
    ...WorkloadBound.args,
    workload: { ...INSTALL_WORKLOAD_PROPS.artifacts!, treeDigest: "9".repeat(64) },
  },
};
export const WorkloadGerman: Story = { args: WorkloadBound.args, render: German.render };
export const WorkloadSpanish: Story = { args: WorkloadBound.args, render: Spanish.render };
export const WorkloadFrench: Story = { args: WorkloadBound.args, render: French.render };
export const WorkloadPortuguese: Story = { args: WorkloadBound.args, render: Portuguese.render };
export const WorkloadJapanese: Story = { args: WorkloadBound.args, render: Japanese.render };
export const WorkloadKorean: Story = { args: WorkloadBound.args, render: Korean.render };
export const WorkloadChinese: Story = { args: WorkloadBound.args, render: Chinese.render };
