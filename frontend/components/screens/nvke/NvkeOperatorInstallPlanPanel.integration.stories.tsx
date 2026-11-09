import { useEffect, useMemo } from "react";
import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { useNvkeInstallPlan } from "@/graphql/nvke/use-nvke-install-plan";
import { readInstallPlanJournal } from "@/lib/nvke/install-plan-recovery";
import { nativeTextDigest } from "@/lib/nvke/recovery";
import { NvkeOperatorInstallPlanPanel } from "./NvkeOperatorInstallPlanPanel";
import { INSTALL_PLAN_PROPS as fixture } from "./NvkeOperatorInstallPlanPanel.stories";
import { OPERATOR_JOB_PROPS } from "./NvkeOperatorJobPanel.stories";
const plan = fixture.plan!;
const context = {
  scope: { server: "http://127.0.0.1/fixture-graphql/", organizationId: plan.siteId, actorId: 41 },
  scopeKey: "controlled-plan-recovery",
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
};
declare global {
  interface Window {
    __NVKE_PLAN_FIXTURE_CALLS__?: {
      name: string;
      hasPrivateText: boolean;
      inputSha256?: string;
      journalBeforeCall?: boolean;
    }[];
  }
}
function HookPanel() {
  const panel = useNvkeInstallPlan({
    context,
    source: { ...OPERATOR_JOB_PROPS, operation: fixture.operation },
    pending: false,
  });
  return panel ? <NvkeOperatorInstallPlanPanel {...panel} /> : null;
}
function createControlledClient(retainBeforeLoss: boolean) {
  let attempted = false,
    retained: string | null = null;
  const calls: NonNullable<Window["__NVKE_PLAN_FIXTURE_CALLS__"]> = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink(
      (request) =>
        new Observable((observer) => {
          void (async () => {
            const input = request.variables.input ?? {};
            const text = typeof input.requestText === "string" ? input.requestText : null;
            const record = {
              name: request.operationName,
              hasPrivateText: text !== null,
              ...(text
                ? {
                    inputSha256: await nativeTextDigest(text),
                    journalBeforeCall: !!readInstallPlanJournal(
                      window.localStorage,
                      binding,
                      plan.id
                    ),
                  }
                : {}),
            };
            calls.push(record);
            let field: string;
            if (request.operationName === "PrepareNvkeInstallPlan") {
              field = "prepareNvkeOperatorInstallPlan";
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
              request.operationName === "ReviewNvkeInstallPlan" ||
              request.operationName === "RetryNvkeInstallPlan"
            ) {
              field =
                request.operationName === "ReviewNvkeInstallPlan"
                  ? "reviewNvkeOperatorInstallPlan"
                  : "recoverNvkeOperatorInstallPlanPreparation";
            } else {
              const items = request.operationName === "GetNvkeInstallPlanItemPage";
              field = items
                ? "astroliftNvkeOperatorInstallPlanItemPage"
                : "astroliftNvkeOperatorInstallPlanWorkloadPage";
              const search = String(request.variables.search ?? "");
              const rows = (items ? fixture.itemTable.rows : fixture.workloadTable.rows).filter(
                (row) => JSON.stringify(row).includes(search)
              );
              observer.next({
                data: {
                  [field]: {
                    ok: true,
                    errors: [],
                    data: {
                      planId: plan.id,
                      planDigest: plan.plan.planDigest,
                      view: items ? "items" : "workloads",
                      items: rows,
                      totalCount: rows.length,
                      nextCursor: null,
                    },
                  },
                },
              });
              observer.complete();
              return;
            }
            observer.next({
              data: {
                [field]:
                  retained !== null
                    ? { ok: true, errors: [], data: plan }
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
    window.__NVKE_PLAN_FIXTURE_CALLS__ = server.calls;
    return () => {
      if (window.__NVKE_PLAN_FIXTURE_CALLS__ === server.calls)
        delete window.__NVKE_PLAN_FIXTURE_CALLS__;
    };
  }, [server]);
  return (
    <ApolloProvider client={server.client}>
      <HookPanel />
    </ApolloProvider>
  );
}
export default {
  title: "Screens/NVKE/Controlled plan recovery",
  component: ControlledRecovery,
  args: { retainBeforeLoss: false },
  parameters: { nextjs: { appDirectory: true } },
} satisfies Meta<typeof ControlledRecovery>;
type Story = StoryObj<typeof ControlledRecovery>;
export const LostBeforeRetention: Story = {};
export const LostAfterRetention: Story = { args: { retainBeforeLoss: true } };
