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
  installPlanImagePayloadSchema,
  installPlanImmutableExecutionSchema,
  IMMUTABLE_PAYLOAD_STATE,
  IMMUTABLE_EXECUTION_STATE,
} from "@/lib/nvke/operator-install-plan-immutable";
import {
  NvkeOperatorInstallPlanImmutablePanel,
  type NvkeOperatorInstallPlanImmutablePanelProps,
} from "./NvkeOperatorInstallPlanImmutablePanel";

const binding = INSTALL_IMAGE_BINDING_PROPS.artifacts!;
const parent = INSTALL_IMAGE_BINDING_PROPS.plan!;
const keys =
  "version status planId planVersion planDigest chartArtifactId chartArtifactVersion chartArtifactDigest treeId treeVersion treeDigest operationId operationVersion siteId siteVersion authorityId authorityVersion workloadId workloadVersion workloadDigest".split(
    " "
  );
const metadata = {
  ...Object.fromEntries(keys.map((key) => [key, binding[key as keyof typeof binding]])),
  id: "709813b1-a359-465f-bc15-41b648ece1b1",
  bindingId: binding.id,
  bindingVersion: binding.version,
  bindingDigest: binding.bindingDigest,
  artifactDigest: "1".repeat(64),
  sourceDigest: "2".repeat(64),
  captureDigest: "3".repeat(64),
  fileCount: 1,
  observedAt: binding.observedAt,
  sourceObservedAt: binding.observedAt,
  continuationAuthorized: false,
};
export const INSTALL_IMMUTABLE_PAYLOAD_PROPS: NvkeOperatorInstallPlanImmutablePanelProps = {
  ...INSTALL_IMAGE_BINDING_PROPS,
  mode: "payload",
  scopeKey: "storybook-install-immutable-payload",
  binding,
  artifacts: installPlanImagePayloadSchema.parse({
    ...metadata,
    payloadDigest: "8".repeat(64),
    payloadBytes: "64",
    resourceCount: 1,
    imageUseCount: 1,
    rewriteCount: 1,
    executionState: IMMUTABLE_PAYLOAD_STATE,
  }),
  onPrepare: async () => true,
};
export const INSTALL_IMMUTABLE_EXECUTION_PROPS: NvkeOperatorInstallPlanImmutablePanelProps = {
  ...INSTALL_IMMUTABLE_PAYLOAD_PROPS,
  mode: "execution",
  scopeKey: "storybook-install-immutable-execution",
  artifacts: installPlanImmutableExecutionSchema.parse({
    ...metadata,
    id: "5b3bf3bf-1a1a-4b5a-8c75-cfb2520908b3",
    executionDigest: "7".repeat(64),
    bytes: "128",
    phaseCount: parent.itemCount - parent.plan.firstRemainingOrdinal,
    skippedPhaseCount: parent.plan.firstRemainingOrdinal,
    importCount: 0,
    executionState: IMMUTABLE_EXECUTION_STATE,
  }),
};
export default {
  title: "Screens/NVKE/Immutable install artifacts",
  component: NvkeOperatorInstallPlanImmutablePanel,
  excludeStories: ["INSTALL_IMMUTABLE_PAYLOAD_PROPS", "INSTALL_IMMUTABLE_EXECUTION_PROPS"],
  args: INSTALL_IMMUTABLE_PAYLOAD_PROPS,
} satisfies Meta<typeof NvkeOperatorInstallPlanImmutablePanel>;
type Story = StoryObj<typeof NvkeOperatorInstallPlanImmutablePanel>;
export const Payload: Story = {};
export const Execution: Story = { args: INSTALL_IMMUTABLE_EXECUTION_PROPS };
export const Unloaded: Story = { args: { artifacts: null } };
export const UnloadedExecution: Story = {
  args: { ...INSTALL_IMMUTABLE_EXECUTION_PROPS, artifacts: null },
};
export const ParentRequired: Story = { args: { artifacts: null, binding: null } };
export const Loading: Story = { args: { artifacts: null, loading: true } };
export const Pending: Story = { args: { pending: true } };
export const LostReply: Story = {
  args: {
    artifacts: null,
    retainedArtifactId: INSTALL_IMMUTABLE_PAYLOAD_PROPS.artifacts!.id,
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
    retainedArtifactId: INSTALL_IMMUTABLE_PAYLOAD_PROPS.artifacts!.id,
    allowOriginalPreparation: true,
  },
};
export const Narrow: Story = { parameters: { viewport: { defaultViewport: "tablet" } } };
function localized(
  locale: string,
  messages: AbstractIntlMessages,
  args: NvkeOperatorInstallPlanImmutablePanelProps
) {
  return (
    <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
      <NvkeOperatorInstallPlanImmutablePanel {...args} />
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
