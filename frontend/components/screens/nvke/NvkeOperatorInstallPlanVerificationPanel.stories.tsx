import { NextIntlClientProvider, type AbstractIntlMessages } from "next-intl";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import de from "@/messages/de.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import pt from "@/messages/pt-BR.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import { INSTALL_CONTEXT_PROPS } from "./NvkeOperatorInstallPlanContextPanel.stories";
import {
  NvkeOperatorInstallPlanVerificationPanel,
  type NvkeOperatorInstallPlanVerificationPanelProps,
} from "./NvkeOperatorInstallPlanVerificationPanel";

const context = INSTALL_CONTEXT_PROPS.artifacts!;
export const INSTALL_VERIFICATION_PROPS: NvkeOperatorInstallPlanVerificationPanelProps = {
  ...INSTALL_CONTEXT_PROPS,
  scopeKey: "storybook-install-verification",
  context,
  contextSelection: INSTALL_CONTEXT_PROPS.retainedSelection,
  artifacts: {
    id: "690ad665-6588-43da-9614-09e01fbc4a01",
    version: 2,
    status: "prepared",
    planId: context.planId,
    planVersion: context.planVersion,
    planDigest: context.planDigest,
    chartArtifactId: context.chartArtifactId,
    chartArtifactVersion: context.chartArtifactVersion,
    chartArtifactDigest: context.chartArtifactDigest,
    treeId: context.treeId,
    treeVersion: context.treeVersion,
    treeDigest: context.treeDigest,
    contextId: context.id,
    contextVersion: context.version,
    contextDigest: context.contextDigest,
    operationId: context.operationId,
    operationVersion: context.operationVersion,
    siteId: context.siteId,
    siteVersion: context.siteVersion,
    authorityId: context.authorityId,
    authorityVersion: context.authorityVersion,
    item: context.item,
    ordinal: context.ordinal,
    captureDigest: context.captureDigest,
    frozenChartSha256: context.frozenChartSha256,
    artifactDigest: "4".repeat(64),
    verificationDigest: "5".repeat(64),
    requests: 3,
    bytes: "123",
    observedAt: "2026-11-09T16:01:00+00:00",
    continuationAuthorized: false,
    executionState: "requires-phase-use-resource-credential-schema-images-and-linked-admission",
  },
  retainedArtifactId: null,
  canRetry: true,
  onPrepare: async () => true,
  onReview: async () => true,
  onRetryPreparation: async () => true,
};

export default {
  title: "Screens/NVKE/Install render verification",
  component: NvkeOperatorInstallPlanVerificationPanel,
  excludeStories: ["INSTALL_VERIFICATION_PROPS"],
  args: INSTALL_VERIFICATION_PROPS,
} satisfies Meta<typeof NvkeOperatorInstallPlanVerificationPanel>;
type Story = StoryObj<typeof NvkeOperatorInstallPlanVerificationPanel>;
export const Full: Story = {};
export const Unloaded: Story = { args: { artifacts: null } };
export const ContextRequired: Story = { args: { artifacts: null, context: null } };
export const Loading: Story = { args: { artifacts: null, loading: true } };
export const Pending: Story = { args: { pending: true } };
export const LostReply: Story = {
  args: {
    artifacts: null,
    retainedArtifactId: INSTALL_VERIFICATION_PROPS.artifacts!.id,
    error: "UNCONFIRMED",
  },
};
export const OtherReviewer: Story = { args: { canRetry: false } };
export const ReadOnly: Story = { args: { canReview: false } };
export const StaleParent: Story = {
  args: {
    plan: { ...INSTALL_CONTEXT_PROPS.plan!, version: INSTALL_CONTEXT_PROPS.plan!.version + 1 },
  },
};
export const StaleContext: Story = {
  args: { context: { ...context, version: context.version + 1 } },
};
export const WrongContextSelection: Story = {
  args: { contextSelection: { item: "controller", ordinal: 2 } },
};
export const StaleSource: Story = {
  args: {
    operation: {
      ...INSTALL_CONTEXT_PROPS.operation,
      version: INSTALL_CONTEXT_PROPS.operation.version + 1,
    },
  },
};
export const Unverified: Story = {
  args: { authority: { ...INSTALL_CONTEXT_PROPS.authority!, verificationCode: "UNVERIFIED" } },
};
export const OriginalPreparationRecovery: Story = {
  args: {
    artifacts: null,
    retainedArtifactId: INSTALL_VERIFICATION_PROPS.artifacts!.id,
    allowOriginalPreparation: true,
    error: "UNCONFIRMED",
  },
};
export const ChooseAnotherArtifact: Story = { args: { onChooseArtifact: async () => {} } };
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768, maxWidth: "100%" }}>
      <NvkeOperatorInstallPlanVerificationPanel {...args} />
    </div>
  ),
};

function localized(
  locale: string,
  messages: AbstractIntlMessages,
  args: NvkeOperatorInstallPlanVerificationPanelProps
) {
  return (
    <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
      <NvkeOperatorInstallPlanVerificationPanel {...args} />
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
