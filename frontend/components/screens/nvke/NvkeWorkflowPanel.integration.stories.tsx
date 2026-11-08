import { useMemo } from "react";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import type {
  GetNvkeContractQuery,
  GetNvkeOperationQuery,
  GetNvkeSiteQuery,
} from "@/graphql/__generated__/operations";
import { useNvkeWorkflows } from "@/graphql/nvke/use-nvke-workflows";
import { useNvkeMaintenance } from "@/graphql/nvke/use-nvke-maintenance";
import { useNvkeRead } from "@/graphql/nvke/use-scoped-read";
import { GET_NVKE_CONTRACT, GET_NVKE_OPERATION, GET_NVKE_SITE } from "@/graphql/nvke/nvke.queries";
import { NvkeWorkflowPanel } from "./NvkeWorkflowPanel";

type Qualification = {
  endpoint: string;
  token: string;
  organizationId: string;
  actorId: number;
  siteId: string;
  operationId: string;
};
declare global {
  interface Window {
    __NVKE_LOCAL_QUALIFICATION__?: Qualification;
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
  const detail = useNvkeRead<GetNvkeOperationQuery>(
    GET_NVKE_OPERATION,
    { operationId: config.operationId },
    context.scopeKey,
    0,
    config.organizationId
  );
  const catalog = useNvkeRead<GetNvkeContractQuery>(
    GET_NVKE_CONTRACT,
    {},
    context.scopeKey,
    0,
    config.organizationId
  );
  const operation = detail.data?.astroliftNvkeOperation ?? null;
  const enabled =
    !detail.loading &&
    !site.loading &&
    !catalog.loading &&
    !detail.failed &&
    !site.failed &&
    !catalog.failed;
  const maintenance = useNvkeMaintenance({
    context,
    scopeKey: context.scopeKey,
    siteId: config.siteId,
    operation,
    enabled,
  });
  const workflows = useNvkeWorkflows({
    context,
    scopeKey: context.scopeKey,
    site: site.data?.astroliftNvkeSite ?? null,
    operation,
    contract: catalog.data?.astroliftNvkeContract,
    maintenance,
    enabled,
    onRefresh: () => {
      site.refresh();
      detail.refresh();
      catalog.refresh();
    },
  });
  return workflows ? (
    <NvkeWorkflowPanel {...workflows} />
  ) : (
    <p>Waiting for local qualification source.</p>
  );
}

function LocalQualification() {
  const config = typeof window === "undefined" ? undefined : window.__NVKE_LOCAL_QUALIFICATION__;
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
  title: "Screens/NVKE/Local qualification",
  component: LocalQualification,
  parameters: { nextjs: { appDirectory: true } },
} satisfies Meta<typeof LocalQualification>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Connected: Story = {};
