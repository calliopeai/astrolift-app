import { useMemo } from "react";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import type { GetNvkeSiteQuery, GetNvkeOperationQuery } from "@/graphql/__generated__/operations";
import { useNvkeOperatorAgent } from "@/graphql/nvke/use-nvke-operator-agent";
import { useNvkeOperatorWorkbench } from "@/graphql/nvke/use-nvke-operator-workbench";
import { useNvkeInstallPlan } from "@/graphql/nvke/use-nvke-install-plan";
import { useNvkeOperatorInstallReview } from "@/graphql/nvke/use-nvke-operator-install-review";
import { useNvkeOperatorJob } from "@/graphql/nvke/use-nvke-operator-job";
import { useNvkeRead } from "@/graphql/nvke/use-scoped-read";
import { GET_NVKE_SITE, GET_NVKE_OPERATION } from "@/graphql/nvke/nvke.queries";
import { NvkeOperatorAgentPanel } from "./NvkeOperatorAgentPanel";
import { NvkeOperatorWorkbench } from "./NvkeOperatorWorkbench";
import { NvkeOperationScreen } from "./NvkeOperationScreen";

type Qualification = {
  endpoint: string;
  token: string;
  organizationId: string;
  actorId: number;
  siteId: string;
  operationId?: string;
};
declare global {
  interface Window {
    __NVKE_LOCAL_AGENT__?: Qualification;
  }
}
function ConnectedPanel({
  config,
  mode,
}: {
  config: Qualification;
  mode: "authority" | "workbench" | "operation";
}) {
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
  const workbench = useNvkeOperatorWorkbench({
    context,
    scopeKey: context.scopeKey,
    site: site.data?.astroliftNvkeSite ?? null,
    authority: panel?.authority ?? null,
    enabled: mode === "workbench" && !site.loading && !site.failed && !!panel && !panel.loading,
    onRefresh: site.refresh,
    onJournalChange: () => {},
  });
  const operation = useNvkeRead<GetNvkeOperationQuery>(
    GET_NVKE_OPERATION,
    { operationId: config.operationId },
    mode === "operation" && config.operationId
      ? JSON.stringify([context.scopeKey, config.operationId])
      : "",
    0,
    config.organizationId
  );
  const refreshOperation = () => {
    operation.refresh();
    site.refresh();
  };
  const job = useNvkeOperatorJob({
    context,
    scopeKey: context.scopeKey,
    site: site.data?.astroliftNvkeSite ?? null,
    operation: operation.data?.astroliftNvkeOperation ?? null,
    enabled:
      mode === "operation" &&
      !site.loading &&
      !site.failed &&
      !operation.loading &&
      !operation.failed,
    onRefresh: refreshOperation,
  });
  const installReview = useNvkeOperatorInstallReview({
    context,
    source: job,
    pending: job?.pending ?? false,
    onRefresh: refreshOperation,
  });
  const installPlan = useNvkeInstallPlan({
    context,
    source: job,
    pending: Boolean(job?.pending || installReview?.pending),
  });
  if (mode === "operation")
    return (
      <NvkeOperationScreen
        operation={operation.data?.astroliftNvkeOperation ?? null}
        siteName={site.data?.astroliftNvkeSite?.name ?? ""}
        loading={site.loading || operation.loading}
        error={
          site.failed || operation.failed ? "Local qualification source is unavailable." : null
        }
        pending={Boolean(job?.pending || installReview?.pending || installPlan?.pending)}
        canAct={context.access.admin}
        canResume={false}
        onRefresh={refreshOperation}
        onCancel={async () => false}
        onResume={async () => false}
        onDownload={async () => {}}
        operatorJob={job}
        operatorInstallReview={installReview}
        operatorInstallPlan={installPlan}
      />
    );
  if (mode === "workbench")
    return workbench ? (
      <NvkeOperatorWorkbench {...workbench} />
    ) : (
      <p>Waiting for local qualification source.</p>
    );
  return panel ? (
    <NvkeOperatorAgentPanel {...panel} />
  ) : (
    <p>Waiting for local qualification source.</p>
  );
}
function LocalQualification({
  mode = "authority",
}: {
  mode?: "authority" | "workbench" | "operation";
}) {
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
      <ConnectedPanel config={config} mode={mode} />
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
export const Workbench: Story = { args: { mode: "workbench" } };
export const Operation: Story = { args: { mode: "operation" } };
