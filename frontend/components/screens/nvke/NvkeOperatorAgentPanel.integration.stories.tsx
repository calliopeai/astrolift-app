import { useMemo } from "react";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import type { GetNvkeSiteQuery } from "@/graphql/__generated__/operations";
import { useNvkeOperatorAgent } from "@/graphql/nvke/use-nvke-operator-agent";
import { useNvkeRead } from "@/graphql/nvke/use-scoped-read";
import { GET_NVKE_SITE } from "@/graphql/nvke/nvke.queries";
import { NvkeOperatorAgentPanel } from "./NvkeOperatorAgentPanel";

type Qualification = {
  endpoint: string;
  token: string;
  organizationId: string;
  actorId: number;
  siteId: string;
};
declare global {
  interface Window {
    __NVKE_LOCAL_AGENT__?: Qualification;
  }
}
function ConnectedPanel({ config }: { config: Qualification }) {
  const context = {
    scope: {
      server: config.endpoint,
      organizationId: config.organizationId,
      actorId: config.actorId,
    },
    scopeKey: JSON.stringify([config.endpoint, config.organizationId, config.actorId]),
    access: { read: true, manage: false, admin: true, loading: false },
    loading: false,
    failed: false,
  };
  const site = useNvkeRead<GetNvkeSiteQuery>(
    GET_NVKE_SITE,
    { siteId: config.siteId },
    context.scopeKey,
    0,
    config.organizationId
  );
  const panel = useNvkeOperatorAgent({
    context,
    scopeKey: context.scopeKey,
    site: site.data?.astroliftNvkeSite ?? null,
    enabled: !site.loading && !site.failed,
    onSiteRefresh: site.refresh,
  });
  return panel ? (
    <NvkeOperatorAgentPanel {...panel} />
  ) : (
    <p>Waiting for local qualification source.</p>
  );
}
function LocalQualification() {
  const config = typeof window === "undefined" ? undefined : window.__NVKE_LOCAL_AGENT__;
  const client = useMemo(() => {
    if (!config) return null;
    const endpoint = new URL(config.endpoint);
    if (
      window.location.hostname !== "127.0.0.1" ||
      endpoint.hostname !== "127.0.0.1" ||
      endpoint.protocol !== "http:" ||
      endpoint.username ||
      endpoint.password ||
      endpoint.search ||
      endpoint.hash
    )
      throw new Error("Local qualification requires owned loopback origins");
    return new ApolloClient({
      cache: new InMemoryCache(),
      link: new HttpLink({
        uri: endpoint.href,
        credentials: "omit",
        headers: {
          Authorization: `Bearer ${config.token}`,
          "X-Astrolift-Organization": config.organizationId,
        },
      }),
    });
  }, [config]);
  return client && config ? (
    <ApolloProvider client={client}>
      <ConnectedPanel config={config} />
    </ApolloProvider>
  ) : (
    <p>Requires an owned local backend qualification runner.</p>
  );
}
const meta = {
  title: "Screens/NVKE/Local agent qualification",
  component: LocalQualification,
  parameters: { nextjs: { appDirectory: true } },
} satisfies Meta<typeof LocalQualification>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Connected: Story = {};
