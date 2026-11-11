import { NextIntlClientProvider, type AbstractIntlMessages } from "next-intl";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import de from "@/messages/de.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import pt from "@/messages/pt-BR.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import { INSTALL_PULL_AUTHORITY_PROPS } from "./NvkeOperatorInstallPlanPullAuthorityPanel.stories";
import {
  installPlanPullSecretAnalysisSchema,
  PULL_SECRET_ANALYSIS_STATE,
} from "@/lib/nvke/operator-install-plan-pull-secret-analysis";
import {
  NvkeOperatorInstallPlanPullSecretAnalysisPanel,
  type NvkeOperatorInstallPlanPullSecretAnalysisPanelProps,
} from "./NvkeOperatorInstallPlanPullSecretAnalysisPanel";

const binding = INSTALL_PULL_AUTHORITY_PROPS.binding!;
const observation = INSTALL_PULL_AUTHORITY_PROPS.artifacts!;
const keys =
  "version status planId planVersion planDigest chartArtifactId chartArtifactVersion chartArtifactDigest treeId treeVersion treeDigest operationId operationVersion siteId siteVersion authorityId authorityVersion workloadId workloadVersion workloadDigest".split(
    " "
  );
export const INSTALL_PULL_SECRET_ANALYSIS_PROPS: NvkeOperatorInstallPlanPullSecretAnalysisPanelProps =
  {
    ...INSTALL_PULL_AUTHORITY_PROPS,
    scopeKey: "storybook-install-pull-secret-analysis",
    binding,
    pullAuthority: observation,
    retainedArtifactId: null,
    artifacts: installPlanPullSecretAnalysisSchema.parse({
      ...Object.fromEntries(keys.map((key) => [key, binding[key as keyof typeof binding]])),
      id: "31d86755-ef24-450e-a1e6-c6a06906cc95",
      bindingId: binding.id,
      bindingVersion: binding.version,
      bindingDigest: binding.bindingDigest,
      artifactDigest: "1".repeat(64),
      sourceDigest: "2".repeat(64),
      captureDigest: "3".repeat(64),
      authorityDigest: "8".repeat(64),
      pullAuthorityId: observation.id,
      pullAuthorityVersion: observation.version,
      pullAuthorityDigest: observation.authorityDigest,
      sourceObservedAt: observation.observedAt,
      analyzedAt: observation.observedAt,
      imageUseCount: 2,
      candidateCount: 2,
      missingReferenceCount: 1,
      executionState: PULL_SECRET_ANALYSIS_STATE,
      continuationAuthorized: false,
    }),
    onPrepare: async () => true,
  };
export default {
  title: "Screens/NVKE/Image credential candidates",
  component: NvkeOperatorInstallPlanPullSecretAnalysisPanel,
  excludeStories: ["INSTALL_PULL_SECRET_ANALYSIS_PROPS"],
  args: INSTALL_PULL_SECRET_ANALYSIS_PROPS,
} satisfies Meta<typeof NvkeOperatorInstallPlanPullSecretAnalysisPanel>;
type Story = StoryObj<typeof NvkeOperatorInstallPlanPullSecretAnalysisPanel>;
export const Interpreted: Story = {};
export const NoSecretCandidates: Story = {
  args: { artifacts: { ...INSTALL_PULL_SECRET_ANALYSIS_PROPS.artifacts!, candidateCount: 0 } },
};
export const PullObservationRequired: Story = { args: { artifacts: null, pullAuthority: null } };
export const StalePullObservation: Story = {
  args: { pullAuthority: { ...observation, version: observation.version + 1 } },
};
export const Unloaded: Story = { args: { artifacts: null } };
export const PrepareLostReply: Story = { args: { artifacts: null, onPrepare: async () => false } };
export const ParentRequired: Story = { args: { artifacts: null, binding: null } };
export const Loading: Story = { args: { artifacts: null, loading: true } };
export const Pending: Story = { args: { pending: true } };
export const LostReply: Story = {
  args: {
    artifacts: null,
    retainedArtifactId: INSTALL_PULL_SECRET_ANALYSIS_PROPS.artifacts!.id,
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
      ...INSTALL_PULL_AUTHORITY_PROPS.operation,
      version: INSTALL_PULL_AUTHORITY_PROPS.operation.version + 1,
    },
  },
};
export const Unverified: Story = {
  args: {
    authority: { ...INSTALL_PULL_AUTHORITY_PROPS.authority!, verificationCode: "UNVERIFIED" },
  },
};
export const OriginalPreparationRecovery: Story = {
  args: {
    artifacts: null,
    retainedArtifactId: INSTALL_PULL_SECRET_ANALYSIS_PROPS.artifacts!.id,
    allowOriginalPreparation: true,
  },
};
export const ChooseAnotherArtifact: Story = { args: { onChooseArtifact: async () => {} } };
export const Narrow: Story = { parameters: { viewport: { defaultViewport: "tablet" } } };
function localized(
  locale: string,
  messages: AbstractIntlMessages,
  args: NvkeOperatorInstallPlanPullSecretAnalysisPanelProps
) {
  return (
    <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
      <NvkeOperatorInstallPlanPullSecretAnalysisPanel {...args} />
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
