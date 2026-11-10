import { useMemo } from "react";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { useNvkeInstallPlanVerification } from "@/graphql/nvke/use-nvke-install-plan-verification";
import type { NvkeBrowserScope } from "@/lib/nvke/recovery";
import { NvkeOperatorInstallPlanVerificationPanel } from "./NvkeOperatorInstallPlanVerificationPanel";
import type { NvkeOperatorInstallPlanContextPanelProps } from "./NvkeOperatorInstallPlanContextPanel";
import { INSTALL_CONTEXT_PROPS } from "./NvkeOperatorInstallPlanContextPanel.stories";

export type VerificationApiFixture = {
  scope: NvkeBrowserScope;
  source: Partial<NvkeOperatorInstallPlanContextPanelProps>;
};
declare global {
  interface Window {
    __NVKE_VERIFICATION_API_FIXTURE__?: VerificationApiFixture;
  }
}
function HookPanel({ fixture }: { fixture: VerificationApiFixture }) {
  const panel = useNvkeInstallPlanVerification({
    context: {
      scope: fixture.scope,
      scopeKey: "owned-verification-api-browser",
      access: { read: true, manage: false, admin: true, loading: false },
      loading: false,
      failed: false,
    },
    source: {
      ...INSTALL_CONTEXT_PROPS,
      ...fixture.source,
      site: { ...INSTALL_CONTEXT_PROPS.site, ...fixture.source.site },
      operation: { ...INSTALL_CONTEXT_PROPS.operation, ...fixture.source.operation },
      authority: { ...INSTALL_CONTEXT_PROPS.authority!, ...fixture.source.authority },
    },
    pending: false,
  });
  return panel ? <NvkeOperatorInstallPlanVerificationPanel {...panel} /> : null;
}
function ApiRecovery() {
  const fixture =
    typeof window === "undefined" ? undefined : window.__NVKE_VERIFICATION_API_FIXTURE__;
  const client = useMemo(
    () =>
      new ApolloClient({
        cache: new InMemoryCache(),
        link: new HttpLink({
          uri: "/__nvke-verification-api",
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
  title: "Screens/NVKE/Owned API verification recovery",
  component: ApiRecovery,
  parameters: { nextjs: { appDirectory: true } },
} satisfies Meta<typeof ApiRecovery>;
type Story = StoryObj<typeof ApiRecovery>;
export const Recovery: Story = {};
