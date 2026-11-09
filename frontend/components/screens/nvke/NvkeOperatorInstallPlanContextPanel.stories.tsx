import { NextIntlClientProvider, type AbstractIntlMessages } from "next-intl";
import de from "@/messages/de.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import pt from "@/messages/pt-BR.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import {
  NvkeOperatorInstallPlanContextPanel,
  type NvkeOperatorInstallPlanContextPanelProps,
} from "./NvkeOperatorInstallPlanContextPanel";
import { INSTALL_TREE_PROPS } from "./NvkeOperatorInstallPlanTreePanel.stories";

const parent = INSTALL_TREE_PROPS.plan!,
  charts = INSTALL_TREE_PROPS.charts!,
  tree = INSTALL_TREE_PROPS.artifacts!;
export const INSTALL_CONTEXT_PROPS: NvkeOperatorInstallPlanContextPanelProps = {
  ...INSTALL_TREE_PROPS,
  scopeKey: "storybook-install-context",
  tree,
  retainedSelection: { item: "nktp", ordinal: 1 },
  artifacts: {
    id: "590ad665-6588-43da-9614-09e01fbc4a01",
    version: 2,
    status: "prepared",
    planId: parent.id,
    planVersion: parent.version,
    planDigest: parent.plan.planDigest,
    chartArtifactId: charts.id,
    chartArtifactVersion: charts.version,
    chartArtifactDigest: charts.artifactDigest,
    treeId: tree.id,
    treeVersion: tree.version,
    treeDigest: tree.treeDigest,
    operationId: parent.operationId,
    operationVersion: parent.operationVersion,
    siteId: parent.siteId,
    siteVersion: parent.siteVersion,
    authorityId: parent.authorityId,
    authorityVersion: parent.authorityVersion,
    item: "nktp",
    ordinal: 1,
    captureDigest: "1".repeat(64),
    frozenChartSha256: "2".repeat(64),
    contextDigest: "3".repeat(64),
    documentCount: 1,
    lookupCount: 0,
    observedAt: "2026-11-09T16:00:00+00:00",
    executionState: "requires-authenticated-phase-verification-images-and-linked-admission",
    continuationAuthorized: false,
  },
};
export default {
  title: "Screens/NVKE/Install render context",
  component: NvkeOperatorInstallPlanContextPanel,
  excludeStories: ["INSTALL_CONTEXT_PROPS"],
  args: INSTALL_CONTEXT_PROPS,
} satisfies Meta<typeof NvkeOperatorInstallPlanContextPanel>;
type Story = StoryObj<typeof NvkeOperatorInstallPlanContextPanel>;
export const Full: Story = {};
export const Unloaded: Story = { args: { artifacts: null, retainedSelection: null } };
export const TreeRequired: Story = { args: { tree: null, artifacts: null } };
export const Loading: Story = { args: { artifacts: null, loading: true } };
export const Pending: Story = { args: { pending: true } };
export const LostReply: Story = {
  args: {
    artifacts: null,
    retainedArtifactId: INSTALL_CONTEXT_PROPS.artifacts!.id,
    error: "UNCONFIRMED",
  },
};
export const OtherReviewer: Story = { args: { canRetry: false } };
export const ReadOnly: Story = { args: { canReview: false } };
export const StaleParent: Story = { args: { plan: { ...parent, version: parent.version + 1 } } };
export const StaleTree: Story = { args: { tree: { ...tree, version: tree.version + 1 } } };
export const StaleSource: Story = {
  args: { operation: { ...INSTALL_TREE_PROPS.operation, version: parent.operationVersion + 1 } },
};
export const Unverified: Story = {
  args: { authority: { ...INSTALL_TREE_PROPS.authority!, verificationCode: "UNVERIFIED" } },
};
export const WrongItem: Story = { args: { retainedSelection: { item: "controller", ordinal: 2 } } };
export const OriginalPreparationRecovery: Story = {
  args: {
    artifacts: null,
    retainedArtifactId: INSTALL_CONTEXT_PROPS.artifacts!.id,
    allowOriginalPreparation: true,
    error: "UNCONFIRMED",
  },
};
export const ChooseAnotherArtifact: Story = { args: { onChooseArtifact: async () => {} } };
export const Width768: Story = {
  render: (args) => (
    <div style={{ width: 768, maxWidth: "100%" }}>
      <NvkeOperatorInstallPlanContextPanel {...args} />
    </div>
  ),
};

function localized(
  locale: string,
  messages: AbstractIntlMessages,
  args: NvkeOperatorInstallPlanContextPanelProps
) {
  return (
    <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
      <NvkeOperatorInstallPlanContextPanel {...args} />
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
