import { NextIntlClientProvider, type AbstractIntlMessages } from "next-intl";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import de from "@/messages/de.json";
import fr from "@/messages/fr.json";
import pt from "@/messages/pt-BR.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { INSTALL_PULL_SECRET_ANALYSIS_PROPS as analysis } from "./NvkeOperatorInstallPlanPullSecretAnalysisPanel.stories";
import { INSTALL_NODE_PLATFORMS_PROPS as nodes } from "./NvkeOperatorInstallPlanNodePlatformsPanel.stories";
import {
  PULL_RUNTIME_STATE,
  type NvkeInstallPlanPullRuntime,
} from "@/lib/nvke/operator-install-plan-pull-runtime";
import {
  NvkeOperatorInstallPlanPullRuntimePanel,
  type NvkeOperatorInstallPlanPullRuntimePanelProps,
} from "./NvkeOperatorInstallPlanPullRuntimePanel";

export const INSTALL_PULL_RUNTIME_PROPS: NvkeOperatorInstallPlanPullRuntimePanelProps = {
  ...analysis,
  scopeKey: "storybook-install-pull-runtime",
  analysis: analysis.artifacts!,
  nodePlatforms: nodes.artifacts!,
  correspondence: null,
  onReview: async () => true,
};
export const PULL_RUNTIME_RESULT: NvkeInstallPlanPullRuntime = {
  analysis: analysis.artifacts!,
  nodePlatforms: nodes.artifacts!,
  correspondenceDigest: "a".repeat(64),
  imageUseCount: analysis.artifacts!.imageUseCount,
  nodeCount: 1,
  sourceObservedAt: analysis.artifacts!.sourceObservedAt,
  nodeObservedAt: nodes.artifacts!.observedAt,
  executionState: PULL_RUNTIME_STATE,
  continuationAuthorized: false as const,
};
export default {
  title: "Screens/NVKE/Pod node pull correspondence",
  component: NvkeOperatorInstallPlanPullRuntimePanel,
  excludeStories: ["INSTALL_PULL_RUNTIME_PROPS", "PULL_RUNTIME_RESULT"],
  args: INSTALL_PULL_RUNTIME_PROPS,
} satisfies Meta<typeof NvkeOperatorInstallPlanPullRuntimePanel>;
type Story = StoryObj<typeof NvkeOperatorInstallPlanPullRuntimePanel>;
export const Ready: Story = {};
export const Matched: Story = { args: { correspondence: PULL_RUNTIME_RESULT } };
export const ParentsRequired: Story = { args: { analysis: null, nodePlatforms: null } };
export const Loading: Story = { args: { loading: true } };
export const Pending: Story = { args: { pending: true } };
export const ReadOnly: Story = { args: { canReview: false } };
export const Unconfirmed: Story = { args: { error: "UNCONFIRMED", onReview: async () => false } };
export const StaleAnalysis: Story = {
  args: {
    analysis: { ...analysis.artifacts!, version: analysis.artifacts!.version + 1 },
    correspondence: PULL_RUNTIME_RESULT,
  },
};
export const StaleNodeCapture: Story = {
  args: {
    nodePlatforms: { ...nodes.artifacts!, version: nodes.artifacts!.version + 1 },
    correspondence: PULL_RUNTIME_RESULT,
  },
};
export const StaleOperation: Story = {
  args: {
    operation: { ...analysis.operation, version: analysis.operation.version + 1 },
    correspondence: PULL_RUNTIME_RESULT,
  },
};
export const ReplacedAuthority: Story = {
  args: {
    authority: { ...analysis.authority!, version: analysis.authority!.version + 1 },
    correspondence: PULL_RUNTIME_RESULT,
  },
};
export const LostReply: Story = { args: { onReview: async () => false } };
export const Narrow: Story = {
  parameters: { viewport: { defaultViewport: "mobile1" } },
  args: Matched.args,
};

function localized(locale: string, messages: AbstractIntlMessages): Story {
  return {
    args: Matched.args,
    render: (args) => (
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
        <NvkeOperatorInstallPlanPullRuntimePanel {...args} />
      </NextIntlClientProvider>
    ),
  };
}
export const English = localized("en", en);
export const Spanish = localized("es", es);
export const German = localized("de", de);
export const French = localized("fr", fr);
export const Portuguese = localized("pt-BR", pt);
export const Japanese = localized("ja", ja);
export const Korean = localized("ko", ko);
export const Chinese = localized("zh-Hans", zh);
