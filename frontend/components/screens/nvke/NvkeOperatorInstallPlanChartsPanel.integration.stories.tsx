import { useEffect, useMemo } from "react";
import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { useNvkeInstallPlanCharts } from "@/graphql/nvke/use-nvke-install-plan-charts";
import { readInstallPlanChartsJournal } from "@/lib/nvke/install-plan-charts-recovery";
import { nativeTextDigest } from "@/lib/nvke/recovery";
import { NvkeOperatorInstallPlanChartsPanel } from "./NvkeOperatorInstallPlanChartsPanel";
import { INSTALL_CHARTS_PROPS as fixture } from "./NvkeOperatorInstallPlanChartsPanel.stories";
import { INSTALL_PLAN_PROPS } from "./NvkeOperatorInstallPlanPanel.stories";
const plan = fixture.plan!;
const context = {
  scope: { server: "http://127.0.0.1/fixture-graphql/", organizationId: plan.siteId, actorId: 41 },
  scopeKey: "controlled-chart-recovery",
  access: { read: true, manage: false, admin: true, loading: false },
  loading: false,
  failed: false,
};
const binding = {
  ...context.scope,
  siteId: plan.siteId,
  siteVersion: plan.siteVersion,
  operationId: plan.operationId,
  operationVersion: plan.operationVersion,
  authorityId: plan.authorityId,
  authorityVersion: plan.authorityVersion,
  instanceId: plan.plan.source.instanceId,
  bundleManifestSha256: plan.plan.source.bundleManifestSha256,
  nativeRevision: plan.plan.source.nativeRevision,
  planId: plan.id,
  planVersion: plan.version,
  planDigest: plan.plan.planDigest,
};
declare global {
  interface Window {
    __NVKE_CHART_FIXTURE_CALLS__?: {
      name: string;
      hasPrivateText: boolean;
      inputSha256?: string;
      journalBeforeCall?: boolean;
    }[];
  }
}
function HookPanel() {
  const panel = useNvkeInstallPlanCharts({
    context,
    source: INSTALL_PLAN_PROPS,
    pending: false,
  });
  return panel ? <NvkeOperatorInstallPlanChartsPanel {...panel} /> : null;
}
function createControlledClient(retainBeforeLoss: boolean) {
  let attempted = false,
    retained: string | null = null;
  const calls: NonNullable<Window["__NVKE_CHART_FIXTURE_CALLS__"]> = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink(
      (request) =>
        new Observable((observer) => {
          void (async () => {
            const input = request.variables.input ?? {};
            const text = typeof input.requestText === "string" ? input.requestText : null;
            const record = {
              name: request.operationName ?? "ANONYMOUS",
              hasPrivateText: text !== null,
              ...(text
                ? {
                    inputSha256: await nativeTextDigest(text),
                    journalBeforeCall: !!readInstallPlanChartsJournal(
                      window.localStorage,
                      binding,
                      fixture.artifacts!.id
                    ),
                  }
                : {}),
            };
            calls.push(record);
            let field: string;
            if (request.operationName === "PrepareNvkeInstallPlanCharts") {
              field = "prepareNvkeOperatorInstallPlanCharts";
              if (!attempted) {
                attempted = true;
                if (retainBeforeLoss) retained = text;
                observer.error(new Error("Controlled lost reply"));
                return;
              }
              if (retained !== null && retained !== text) {
                observer.next({
                  data: { [field]: { ok: false, errors: [{ code: "CONFLICT" }], data: null } },
                });
                observer.complete();
                return;
              }
              retained = text;
            } else if (
              request.operationName === "ReviewNvkeInstallPlanCharts" ||
              request.operationName === "RetryNvkeInstallPlanCharts"
            ) {
              field =
                request.operationName === "ReviewNvkeInstallPlanCharts"
                  ? "reviewNvkeOperatorInstallPlanCharts"
                  : "recoverNvkeOperatorInstallPlanChartsPreparation";
            } else {
              observer.error(new Error("Unexpected chart operation"));
              return;
            }
            observer.next({
              data: {
                [field]:
                  retained !== null
                    ? { ok: true, errors: [], data: fixture.artifacts }
                    : { ok: false, errors: [{ code: "NOT_FOUND" }], data: null },
              },
            });
            observer.complete();
          })().catch((error) => observer.error(error));
        })
    ),
  });
  return { client, calls };
}
function ControlledRecovery({ retainBeforeLoss }: { retainBeforeLoss: boolean }) {
  const server = useMemo(() => createControlledClient(retainBeforeLoss), [retainBeforeLoss]);
  useEffect(() => {
    window.__NVKE_CHART_FIXTURE_CALLS__ = server.calls;
    return () => {
      if (window.__NVKE_CHART_FIXTURE_CALLS__ === server.calls)
        delete window.__NVKE_CHART_FIXTURE_CALLS__;
    };
  }, [server]);
  return (
    <ApolloProvider client={server.client}>
      <HookPanel />
    </ApolloProvider>
  );
}
export default {
  title: "Screens/NVKE/Controlled chart recovery",
  component: ControlledRecovery,
  args: { retainBeforeLoss: false },
  parameters: { nextjs: { appDirectory: true } },
} satisfies Meta<typeof ControlledRecovery>;
type Story = StoryObj<typeof ControlledRecovery>;
export const LostBeforeRetention: Story = {};
export const LostAfterRetention: Story = { args: { retainBeforeLoss: true } };
