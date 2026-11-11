import { useMemo } from "react";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { useNvkeInstallPlanPullRuntime } from "@/graphql/nvke/use-nvke-install-plan-pull-runtime";
import type { PullSecretAnalysisApiFixture } from "./NvkeOperatorInstallPlanPullSecretAnalysisPanel.api.stories";
import type { NvkeInstallPlanPullSecretAnalysis } from "@/lib/nvke/operator-install-plan-pull-secret-analysis";
import type { NvkeInstallPlanNodePlatforms } from "@/lib/nvke/operator-install-plan-node-platforms";
import { NvkeOperatorInstallPlanPullRuntimePanel } from "./NvkeOperatorInstallPlanPullRuntimePanel";
import { INSTALL_TREE_PROPS } from "./NvkeOperatorInstallPlanTreePanel.stories";
import { INSTALL_WORKLOAD_PROPS } from "./NvkeOperatorInstallPlanWorkloadPanel.stories";
import { INSTALL_IMAGE_BINDING_PROPS } from "./NvkeOperatorInstallPlanImageBindingPanel.stories";
import { INSTALL_PULL_AUTHORITY_PROPS } from "./NvkeOperatorInstallPlanPullAuthorityPanel.stories";
import { INSTALL_PULL_SECRET_ANALYSIS_PROPS } from "./NvkeOperatorInstallPlanPullSecretAnalysisPanel.stories";
import { INSTALL_NODE_PLATFORMS_PROPS } from "./NvkeOperatorInstallPlanNodePlatformsPanel.stories";

export type PullRuntimeApiFixture = PullSecretAnalysisApiFixture & {
  analysis: NvkeInstallPlanPullSecretAnalysis;
  nodePlatforms: NvkeInstallPlanNodePlatforms;
};
declare global {
  interface Window {
    __NVKE_PULL_RUNTIME_API_FIXTURE__?: PullRuntimeApiFixture;
  }
}
function HookPanel({ fixture }: { fixture: PullRuntimeApiFixture }) {
  const source = {
    ...INSTALL_TREE_PROPS,
    ...fixture.source,
    site: { ...INSTALL_TREE_PROPS.site, ...fixture.source.site },
    operation: { ...INSTALL_TREE_PROPS.operation, ...fixture.source.operation },
    authority: { ...INSTALL_TREE_PROPS.authority!, ...fixture.source.authority },
  };
  const common = {
    site: source.site,
    operation: source.operation,
    authority: source.authority,
    plan: source.plan,
    charts: source.charts,
    tree: source.artifacts,
    workload: fixture.workload,
    binding: fixture.binding,
    pullAuthority: fixture.pullAuthority,
  };
  const panel = useNvkeInstallPlanPullRuntime({
    context: {
      scope: fixture.scope,
      scopeKey: "owned-pull-runtime-api-browser",
      access: { read: true, manage: false, admin: true, loading: false },
      loading: false,
      failed: false,
    },
    source,
    pending: false,
    workload: { ...INSTALL_WORKLOAD_PROPS, ...common, artifacts: fixture.workload },
    imageBinding: { ...INSTALL_IMAGE_BINDING_PROPS, ...common, artifacts: fixture.binding },
    pullAuthority: { ...INSTALL_PULL_AUTHORITY_PROPS, ...common, artifacts: fixture.pullAuthority },
    analysis: { ...INSTALL_PULL_SECRET_ANALYSIS_PROPS, ...common, artifacts: fixture.analysis },
    nodePlatforms: { ...INSTALL_NODE_PLATFORMS_PROPS, ...common, artifacts: fixture.nodePlatforms },
  });
  return panel ? <NvkeOperatorInstallPlanPullRuntimePanel {...panel} /> : null;
}
function ApiReview() {
  const fixture =
    typeof window === "undefined" ? undefined : window.__NVKE_PULL_RUNTIME_API_FIXTURE__;
  const client = useMemo(
    () =>
      new ApolloClient({
        cache: new InMemoryCache(),
        link: new HttpLink({
          uri: "/__nvke-pull-runtime-api",
          fetchOptions: { redirect: "error", cache: "no-store" },
        }),
      }),
    []
  );
  if (!fixture)
    return <p>Start the owned API qualification driver to supply this story’s source.</p>;
  return (
    <ApolloProvider client={client}>
      <HookPanel fixture={fixture} />
    </ApolloProvider>
  );
}
export default {
  title: "Screens/NVKE/Owned API Pod node correspondence",
  component: ApiReview,
  parameters: { nextjs: { appDirectory: true } },
} satisfies Meta<typeof ApiReview>;
type Story = StoryObj<typeof ApiReview>;
export const Review: Story = {};
