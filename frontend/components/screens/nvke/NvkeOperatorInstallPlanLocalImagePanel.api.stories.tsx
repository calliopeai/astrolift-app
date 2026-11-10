import { useMemo } from "react";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { useNvkeInstallPlanLocalImage } from "@/graphql/nvke/use-nvke-install-plan-local-image";
import type { NvkeBrowserScope } from "@/lib/nvke/recovery";
import { NvkeOperatorInstallPlanLocalImagePanel } from "./NvkeOperatorInstallPlanLocalImagePanel";
import type { NvkeOperatorInstallPlanTreePanelProps } from "./NvkeOperatorInstallPlanTreePanel";
import { INSTALL_TREE_PROPS } from "./NvkeOperatorInstallPlanTreePanel.stories";

export type LocalImageApiFixture = {
  scope: NvkeBrowserScope;
  source: Partial<NvkeOperatorInstallPlanTreePanelProps>;
};
declare global {
  interface Window {
    __NVKE_LOCAL_IMAGE_API_FIXTURE__?: LocalImageApiFixture;
  }
}
function HookPanel({ fixture }: { fixture: LocalImageApiFixture }) {
  const panel = useNvkeInstallPlanLocalImage({
    context: {
      scope: fixture.scope,
      scopeKey: "owned-image-api-browser",
      access: { read: true, manage: false, admin: true, loading: false },
      loading: false,
      failed: false,
    },
    source: {
      ...INSTALL_TREE_PROPS,
      ...fixture.source,
      site: { ...INSTALL_TREE_PROPS.site, ...fixture.source.site },
      operation: { ...INSTALL_TREE_PROPS.operation, ...fixture.source.operation },
      authority: { ...INSTALL_TREE_PROPS.authority!, ...fixture.source.authority },
    },
    pending: false,
  });
  return panel ? <NvkeOperatorInstallPlanLocalImagePanel {...panel} /> : null;
}
function ApiRecovery() {
  const fixture =
    typeof window === "undefined" ? undefined : window.__NVKE_LOCAL_IMAGE_API_FIXTURE__;
  const client = useMemo(
    () =>
      new ApolloClient({
        cache: new InMemoryCache(),
        link: new HttpLink({
          uri: "/__nvke-local-image-api",
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
  title: "Screens/NVKE/Owned API local image recovery",
  component: ApiRecovery,
  parameters: { nextjs: { appDirectory: true } },
} satisfies Meta<typeof ApiRecovery>;
type Story = StoryObj<typeof ApiRecovery>;
export const Recovery: Story = {};
