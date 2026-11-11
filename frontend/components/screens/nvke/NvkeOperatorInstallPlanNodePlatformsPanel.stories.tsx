import { NextIntlClientProvider, type AbstractIntlMessages } from "next-intl";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import de from "@/messages/de.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import pt from "@/messages/pt-BR.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import { INSTALL_IMAGE_BINDING_PROPS } from "./NvkeOperatorInstallPlanImageBindingPanel.stories";
import {
  installPlanNodePlatformsSchema,
  NODE_PLATFORMS_STATE,
} from "@/lib/nvke/operator-install-plan-node-platforms";
import {
  NvkeOperatorInstallPlanNodePlatformsPanel,
  type NvkeOperatorInstallPlanNodePlatformsPanelProps,
} from "./NvkeOperatorInstallPlanNodePlatformsPanel";

const binding = INSTALL_IMAGE_BINDING_PROPS.artifacts!;
const keys =
  "version status planId planVersion planDigest chartArtifactId chartArtifactVersion chartArtifactDigest treeId treeVersion treeDigest operationId operationVersion siteId siteVersion authorityId authorityVersion workloadId workloadVersion workloadDigest".split(
    " "
  );
export const INSTALL_NODE_PLATFORMS_PROPS: NvkeOperatorInstallPlanNodePlatformsPanelProps = {
  ...INSTALL_IMAGE_BINDING_PROPS,
  scopeKey: "storybook-install-node-platforms",
  binding,
  retainedArtifactId: null,
  artifacts: installPlanNodePlatformsSchema.parse({
    ...Object.fromEntries(keys.map((key) => [key, binding[key as keyof typeof binding]])),
    id: "d8185d06-b9ac-4e09-a370-44897f4a72b6",
    bindingId: binding.id,
    bindingVersion: binding.version,
    bindingDigest: binding.bindingDigest,
    artifactDigest: "1".repeat(64),
    sourceDigest: "2".repeat(64),
    captureDigest: "3".repeat(64),
    platformDigest: "7".repeat(64),
    observedAt: binding.observedAt,
    nodeCount: 2,
    contentCount: 1,
    imageReferenceCount: binding.imageCount,
    pairCount: 2,
    issueCount: 8,
    executionState: NODE_PLATFORMS_STATE,
    continuationAuthorized: false,
  }),
  onPrepare: async () => true,
};
export default {
  title: "Screens/NVKE/Node platform observations",
  component: NvkeOperatorInstallPlanNodePlatformsPanel,
  excludeStories: ["INSTALL_NODE_PLATFORMS_PROPS"],
  args: INSTALL_NODE_PLATFORMS_PROPS,
} satisfies Meta<typeof NvkeOperatorInstallPlanNodePlatformsPanel>;
type Story = StoryObj<typeof NvkeOperatorInstallPlanNodePlatformsPanel>;
export const Observed: Story = {};
export const Unloaded: Story = { args: { artifacts: null } };
export const PrepareLostReply: Story = { args: { artifacts: null, onPrepare: async () => false } };
export const ParentRequired: Story = { args: { artifacts: null, binding: null } };
export const Loading: Story = { args: { artifacts: null, loading: true } };
export const Pending: Story = { args: { pending: true } };
export const LostReply: Story = {
  args: {
    artifacts: null,
    retainedArtifactId: INSTALL_NODE_PLATFORMS_PROPS.artifacts!.id,
    error: "UNCONFIRMED",
  },
};
export const OtherReviewer: Story = { args: { canRetry: false } };
export const ReadOnly: Story = { args: { canReview: false } };
export const StaleBinding: Story = {
  args: { binding: { ...binding, version: binding.version + 1 } },
};
export const StaleSource: Story = {
  args: {
    operation: {
      ...INSTALL_IMAGE_BINDING_PROPS.operation,
      version: INSTALL_IMAGE_BINDING_PROPS.operation.version + 1,
    },
  },
};
export const Unverified: Story = {
  args: {
    authority: { ...INSTALL_IMAGE_BINDING_PROPS.authority!, verificationCode: "UNVERIFIED" },
  },
};
export const OriginalPreparationRecovery: Story = {
  args: {
    artifacts: null,
    retainedArtifactId: INSTALL_NODE_PLATFORMS_PROPS.artifacts!.id,
    allowOriginalPreparation: true,
  },
};
export const ChooseAnotherArtifact: Story = { args: { onChooseArtifact: async () => {} } };
export const Narrow: Story = { parameters: { viewport: { defaultViewport: "tablet" } } };
function localized(
  locale: string,
  messages: AbstractIntlMessages,
  args: NvkeOperatorInstallPlanNodePlatformsPanelProps
) {
  return (
    <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
      <NvkeOperatorInstallPlanNodePlatformsPanel {...args} />
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
