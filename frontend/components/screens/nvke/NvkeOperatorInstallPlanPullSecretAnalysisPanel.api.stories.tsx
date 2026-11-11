import { INSTALL_WORKLOAD_PROPS } from "./NvkeOperatorInstallPlanWorkloadPanel.stories";
import { useMemo } from "react";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { useNvkeInstallPlanPullSecretAnalysis } from "@/graphql/nvke/use-nvke-install-plan-pull-secret-analysis";
import type { NvkeInstallPlanWorkload } from "@/lib/nvke/operator-install-plan-workload";
import type { NvkeBrowserScope } from "@/lib/nvke/recovery";
import { NvkeOperatorInstallPlanPullSecretAnalysisPanel } from "./NvkeOperatorInstallPlanPullSecretAnalysisPanel";
import type { NvkeOperatorInstallPlanTreePanelProps } from "./NvkeOperatorInstallPlanTreePanel";
import { INSTALL_TREE_PROPS } from "./NvkeOperatorInstallPlanTreePanel.stories";

import { INSTALL_IMAGE_BINDING_PROPS } from "./NvkeOperatorInstallPlanImageBindingPanel.stories";
import type { NvkeInstallPlanImageBinding } from "@/lib/nvke/operator-install-plan-image-binding";

import { INSTALL_PULL_AUTHORITY_PROPS } from "./NvkeOperatorInstallPlanPullAuthorityPanel.stories";
import type { NvkeInstallPlanPullAuthority } from "@/lib/nvke/operator-install-plan-pull-authority";

export type PullSecretAnalysisApiFixture = {
  pullAuthority: NvkeInstallPlanPullAuthority;
  binding: NvkeInstallPlanImageBinding;
  scope: NvkeBrowserScope;
  workload: NvkeInstallPlanWorkload;
  source: Partial<NvkeOperatorInstallPlanTreePanelProps>;
};
declare global {
  interface Window {
    __NVKE_PULL_SECRET_ANALYSIS_API_FIXTURE__?: PullSecretAnalysisApiFixture;
  }
}
function HookPanel({ fixture }: { fixture: PullSecretAnalysisApiFixture }) {
  const source = {
    ...INSTALL_TREE_PROPS,
    ...fixture.source,
    site: { ...INSTALL_TREE_PROPS.site, ...fixture.source.site },
    operation: { ...INSTALL_TREE_PROPS.operation, ...fixture.source.operation },
    authority: { ...INSTALL_TREE_PROPS.authority!, ...fixture.source.authority },
  };
  const panel = useNvkeInstallPlanPullSecretAnalysis({
    pullAuthority: {
      ...INSTALL_PULL_AUTHORITY_PROPS,
      site: source.site,
      operation: source.operation,
      authority: source.authority,
      plan: source.plan,
      charts: source.charts,
      tree: source.artifacts,
      workload: fixture.workload,
      binding: fixture.binding,
      artifacts: fixture.pullAuthority,
    },
    imageBinding: {
      ...INSTALL_IMAGE_BINDING_PROPS,
      site: source.site,
      operation: source.operation,
      authority: source.authority,
      plan: source.plan,
      charts: source.charts,
      tree: source.artifacts,
      workload: fixture.workload,
      artifacts: fixture.binding,
    },
    context: {
      scope: fixture.scope,
      scopeKey: "owned-pull-secret-analysis-api-browser",
      access: { read: true, manage: false, admin: true, loading: false },
      loading: false,
      failed: false,
    },
    source,
    workload: {
      ...INSTALL_WORKLOAD_PROPS,
      site: source.site,
      operation: source.operation,
      authority: source.authority,
      plan: source.plan,
      charts: source.charts,
      tree: source.artifacts,
      artifacts: fixture.workload,
    },
    pending: false,
  });
  return panel ? <NvkeOperatorInstallPlanPullSecretAnalysisPanel {...panel} /> : null;
}
function ApiRecovery() {
  const fixture =
    typeof window === "undefined" ? undefined : window.__NVKE_PULL_SECRET_ANALYSIS_API_FIXTURE__;
  const client = useMemo(
    () =>
      new ApolloClient({
        cache: new InMemoryCache(),
        link: new HttpLink({
          uri: "/__nvke-pull-secret-analysis-api",
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
  title: "Screens/NVKE/Owned API Secret analysis recovery",
  component: ApiRecovery,
  parameters: { nextjs: { appDirectory: true } },
} satisfies Meta<typeof ApiRecovery>;
type Story = StoryObj<typeof ApiRecovery>;
export const Recovery: Story = {};
