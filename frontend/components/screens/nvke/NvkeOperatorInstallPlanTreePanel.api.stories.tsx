import { useMemo } from "react";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { useNvkeInstallPlanTree } from "@/graphql/nvke/use-nvke-install-plan-tree";
import type { NvkeBrowserScope } from "@/lib/nvke/recovery";
import { NvkeOperatorInstallPlanTreePanel } from "./NvkeOperatorInstallPlanTreePanel";
import type { NvkeOperatorInstallPlanChartsPanelProps } from "./NvkeOperatorInstallPlanChartsPanel";
import { INSTALL_CHARTS_PROPS } from "./NvkeOperatorInstallPlanChartsPanel.stories";

export type TreeApiFixture = {
  scope: NvkeBrowserScope;
  source: Partial<NvkeOperatorInstallPlanChartsPanelProps>;
};
declare global {
  interface Window {
    __NVKE_TREE_API_FIXTURE__?: TreeApiFixture;
  }
}
function HookPanel({ fixture }: { fixture: TreeApiFixture }) {
  const panel = useNvkeInstallPlanTree({
    context: {
      scope: fixture.scope,
      scopeKey: "owned-tree-api-browser",
      access: { read: true, manage: false, admin: true, loading: false },
      loading: false,
      failed: false,
    },
    source: {
      ...INSTALL_CHARTS_PROPS,
      ...fixture.source,
      site: { ...INSTALL_CHARTS_PROPS.site, ...fixture.source.site },
      operation: { ...INSTALL_CHARTS_PROPS.operation, ...fixture.source.operation },
      authority: { ...INSTALL_CHARTS_PROPS.authority!, ...fixture.source.authority },
    },
    pending: false,
  });
  return panel ? <NvkeOperatorInstallPlanTreePanel {...panel} /> : null;
}
function ApiRecovery() {
  const fixture = typeof window === "undefined" ? undefined : window.__NVKE_TREE_API_FIXTURE__;
  const client = useMemo(
    () =>
      new ApolloClient({
        cache: new InMemoryCache(),
        link: new HttpLink({
          uri: "/__nvke-tree-api",
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
  title: "Screens/NVKE/Owned API tree recovery",
  component: ApiRecovery,
  parameters: { nextjs: { appDirectory: true } },
} satisfies Meta<typeof ApiRecovery>;
type Story = StoryObj<typeof ApiRecovery>;
export const Recovery: Story = {};
