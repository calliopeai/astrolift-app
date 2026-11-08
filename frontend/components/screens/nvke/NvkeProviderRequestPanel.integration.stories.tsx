import { useMemo } from "react";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import type {
  GetNvkeSiteQuery,
  GetNvkeOperationQuery,
  GetNvkeContractQuery,
} from "@/graphql/__generated__/operations";
import { useNvkeProviderRequest } from "@/graphql/nvke/use-nvke-provider-request";
import { useNvkeProviders } from "@/graphql/nvke/use-nvke-providers";
import { useNvkeRead } from "@/graphql/nvke/use-scoped-read";
import { GET_NVKE_SITE, GET_NVKE_OPERATION, GET_NVKE_CONTRACT } from "@/graphql/nvke/nvke.queries";
import { NvkeProviderRequestPanel } from "./NvkeProviderRequestPanel";
import { NvkeProvidersPanel } from "./NvkeProvidersPanel";
type ProviderQualification = {
  endpoint: string;
  token: string;
  organizationId: string;
  actorId: number;
  siteId: string;
  observationId?: string;
  admin?: boolean;
};
declare global {
  interface Window {
    __NVKE_LOCAL_PROVIDERS__?: ProviderQualification;
  }
}
function ConnectedPanel({ config }: { config: ProviderQualification }) {
  const context = {
    scope: {
      server: config.endpoint,
      organizationId: config.organizationId,
      actorId: config.actorId,
    },
    scopeKey: JSON.stringify([config.endpoint, config.organizationId, config.actorId]),
    access: { read: true, manage: false, admin: config.admin ?? true, loading: false },
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
  const contract = useNvkeRead<GetNvkeContractQuery>(
    GET_NVKE_CONTRACT,
    {},
    context.scopeKey,
    0,
    config.organizationId
  );
  const observed = useNvkeRead<GetNvkeOperationQuery>(
    GET_NVKE_OPERATION,
    { operationId: config.observationId },
    config.observationId ? context.scopeKey : "",
    0,
    config.organizationId
  );
  const request = useNvkeProviderRequest({
    context,
    scopeKey: context.scopeKey,
    site: site.data?.astroliftNvkeSite ?? null,
    contractRevision: contract.data?.astroliftNvkeContract.revision ?? null,
    enabled: !site.loading && !site.failed,
    onRefresh: () => {
      site.refresh();
      contract.refresh();
    },
    onJournalChange: () => {},
  });
  const providers = useNvkeProviders({
    context,
    scopeKey: context.scopeKey,
    siteId: config.siteId,
    operation: observed.data?.astroliftNvkeOperation ?? null,
    enabled: !!config.observationId && !observed.loading && !observed.failed,
  });
  return (
    <>
      {request ? (
        <NvkeProviderRequestPanel {...request} />
      ) : (
        <p>Waiting for local qualification source.</p>
      )}
      {providers && <NvkeProvidersPanel {...providers} />}
    </>
  );
}
function LocalQualification() {
  const config = typeof window === "undefined" ? undefined : window.__NVKE_LOCAL_PROVIDERS__;
  const client = useMemo(() => {
    if (!config) return null;
    const endpoint = new URL(config.endpoint);
    // Credentials are injected in memory by the owned test runner, never story args or URLs.
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
  title: "Screens/NVKE/Local provider qualification",
  component: LocalQualification,
  parameters: { nextjs: { appDirectory: true } },
} satisfies Meta<typeof LocalQualification>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Connected: Story = {};
