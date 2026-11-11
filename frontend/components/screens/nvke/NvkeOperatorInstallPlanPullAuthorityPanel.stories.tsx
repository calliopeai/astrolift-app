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
  installPlanPullAuthoritySchema,
  PULL_AUTHORITY_STATE,
} from "@/lib/nvke/operator-install-plan-pull-authority";
import {
  NvkeOperatorInstallPlanPullAuthorityPanel,
  type NvkeOperatorInstallPlanPullAuthorityPanelProps,
} from "./NvkeOperatorInstallPlanPullAuthorityPanel";

const binding = INSTALL_IMAGE_BINDING_PROPS.artifacts!;
const keys =
  "version status planId planVersion planDigest chartArtifactId chartArtifactVersion chartArtifactDigest treeId treeVersion treeDigest operationId operationVersion siteId siteVersion authorityId authorityVersion workloadId workloadVersion workloadDigest".split(
    " "
  );
export const INSTALL_PULL_AUTHORITY_PROPS: NvkeOperatorInstallPlanPullAuthorityPanelProps = {
  ...INSTALL_IMAGE_BINDING_PROPS,
  scopeKey: "storybook-install-pull-authority",
  binding,
  retainedArtifactId: null,
  artifacts: installPlanPullAuthoritySchema.parse({
    ...Object.fromEntries(keys.map((key) => [key, binding[key as keyof typeof binding]])),
    id: "21d86755-ef24-450e-a1e6-c6a06906cc95",
    bindingId: binding.id,
    bindingVersion: binding.version,
    bindingDigest: binding.bindingDigest,
    artifactDigest: "1".repeat(64),
    sourceDigest: "2".repeat(64),
    captureDigest: "3".repeat(64),
    authorityDigest: "7".repeat(64),
    observedAt: binding.observedAt,
    podCount: 2,
    serviceAccountCount: 1,
    secretCount: 2,
    missingReferenceCount: 1,
    executionState: PULL_AUTHORITY_STATE,
    continuationAuthorized: false,
  }),
  onPrepare: async () => true,
};
export default {
  title: "Screens/NVKE/Pod pull observations",
  component: NvkeOperatorInstallPlanPullAuthorityPanel,
  excludeStories: ["INSTALL_PULL_AUTHORITY_PROPS"],
  args: INSTALL_PULL_AUTHORITY_PROPS,
} satisfies Meta<typeof NvkeOperatorInstallPlanPullAuthorityPanel>;
type Story = StoryObj<typeof NvkeOperatorInstallPlanPullAuthorityPanel>;
export const Observed: Story = {};
export const Unloaded: Story = { args: { artifacts: null } };
export const PrepareLostReply: Story = { args: { artifacts: null, onPrepare: async () => false } };
export const ParentRequired: Story = { args: { artifacts: null, binding: null } };
export const Loading: Story = { args: { artifacts: null, loading: true } };
export const Pending: Story = { args: { pending: true } };
export const LostReply: Story = {
  args: {
    artifacts: null,
    retainedArtifactId: INSTALL_PULL_AUTHORITY_PROPS.artifacts!.id,
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
    retainedArtifactId: INSTALL_PULL_AUTHORITY_PROPS.artifacts!.id,
    allowOriginalPreparation: true,
  },
};
export const ChooseAnotherArtifact: Story = { args: { onChooseArtifact: async () => {} } };
export const Narrow: Story = { parameters: { viewport: { defaultViewport: "tablet" } } };
function localized(
  locale: string,
  messages: AbstractIntlMessages,
  args: NvkeOperatorInstallPlanPullAuthorityPanelProps
) {
  return (
    <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
      <NvkeOperatorInstallPlanPullAuthorityPanel {...args} />
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
