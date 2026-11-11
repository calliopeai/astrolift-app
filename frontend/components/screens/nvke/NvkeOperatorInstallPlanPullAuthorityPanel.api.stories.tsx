import { INSTALL_WORKLOAD_PROPS } from "./NvkeOperatorInstallPlanWorkloadPanel.stories";
import { useMemo } from "react";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { useNvkeInstallPlanPullAuthority } from "@/graphql/nvke/use-nvke-install-plan-pull-authority";
import type { NvkeInstallPlanWorkload } from "@/lib/nvke/operator-install-plan-workload";
import type { NvkeBrowserScope } from "@/lib/nvke/recovery";
import { NvkeOperatorInstallPlanPullAuthorityPanel } from "./NvkeOperatorInstallPlanPullAuthorityPanel";
import type { NvkeOperatorInstallPlanTreePanelProps } from "./NvkeOperatorInstallPlanTreePanel";
import { INSTALL_TREE_PROPS } from "./NvkeOperatorInstallPlanTreePanel.stories";

import { INSTALL_IMAGE_BINDING_PROPS } from "./NvkeOperatorInstallPlanImageBindingPanel.stories";
import type { NvkeInstallPlanImageBinding } from "@/lib/nvke/operator-install-plan-image-binding";

export type PullAuthorityApiFixture = {
  binding: NvkeInstallPlanImageBinding;
  scope: NvkeBrowserScope;
  workload: NvkeInstallPlanWorkload;
  source: Partial<NvkeOperatorInstallPlanTreePanelProps>;
};
declare global {
  interface Window {
    __NVKE_PULL_AUTHORITY_API_FIXTURE__?: PullAuthorityApiFixture;
  }
}
function HookPanel({ fixture }: { fixture: PullAuthorityApiFixture }) {
  const source = {
    ...INSTALL_TREE_PROPS,
    ...fixture.source,
    site: { ...INSTALL_TREE_PROPS.site, ...fixture.source.site },
    operation: { ...INSTALL_TREE_PROPS.operation, ...fixture.source.operation },
    authority: { ...INSTALL_TREE_PROPS.authority!, ...fixture.source.authority },
  };
  const panel = useNvkeInstallPlanPullAuthority({
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
      scopeKey: "owned-pull-authority-api-browser",
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
  return panel ? <NvkeOperatorInstallPlanPullAuthorityPanel {...panel} /> : null;
}
function ApiRecovery() {
  const fixture =
    typeof window === "undefined" ? undefined : window.__NVKE_PULL_AUTHORITY_API_FIXTURE__;
  const client = useMemo(
    () =>
      new ApolloClient({
        cache: new InMemoryCache(),
        link: new HttpLink({
          uri: "/__nvke-pull-authority-api",
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
  title: "Screens/NVKE/Owned API Pod pull recovery",
  component: ApiRecovery,
  parameters: { nextjs: { appDirectory: true } },
} satisfies Meta<typeof ApiRecovery>;
type Story = StoryObj<typeof ApiRecovery>;
export const Recovery: Story = {};
