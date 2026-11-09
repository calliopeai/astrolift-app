import { useEffect, useMemo } from "react";
import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { useNvkeInstallPlanContext } from "@/graphql/nvke/use-nvke-install-plan-context";
import { readInstallPlanContextJournal } from "@/lib/nvke/install-plan-context-recovery";
import { nativeTextDigest } from "@/lib/nvke/recovery";
import { NvkeOperatorInstallPlanContextPanel } from "./NvkeOperatorInstallPlanContextPanel";
import { INSTALL_CONTEXT_PROPS as fixture } from "./NvkeOperatorInstallPlanContextPanel.stories";
import { INSTALL_TREE_PROPS } from "./NvkeOperatorInstallPlanTreePanel.stories";
const plan = fixture.plan!;
const context = {
  scope: { server: "http://127.0.0.1/fixture-graphql/", organizationId: plan.siteId, actorId: 41 },
  scopeKey: "controlled-context-recovery",
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
  chartArtifactId: fixture.charts!.id,
  chartArtifactVersion: fixture.charts!.version,
  chartArtifactDigest: fixture.charts!.artifactDigest,
  treeId: fixture.tree!.id,
  treeVersion: fixture.tree!.version,
  treeDigest: fixture.tree!.treeDigest,
};
declare global {
  interface Window {
    __NVKE_CONTEXT_FIXTURE_CALLS__?: {
      name: string;
      hasPrivateText: boolean;
      inputSha256?: string;
      journalBeforeCall?: boolean;
    }[];
  }
}
function HookPanel() {
  const panel = useNvkeInstallPlanContext({
    context,
    source: INSTALL_TREE_PROPS,
    pending: false,
  });
  return panel ? <NvkeOperatorInstallPlanContextPanel {...panel} /> : null;
}
function createControlledClient(retainBeforeLoss: boolean) {
  const attempted = new Set<string>();
  const retained = new Map<string, { text: string; data: NonNullable<typeof fixture.artifacts> }>();
  const calls: NonNullable<Window["__NVKE_CONTEXT_FIXTURE_CALLS__"]> = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink(
      (request) =>
        new Observable((observer) => {
          void (async () => {
            const input = request.variables.input ?? {};
            const text = typeof input.requestText === "string" ? input.requestText : null;
            const body = text ? (JSON.parse(text) as { artifactId: string }) : null;
            const artifactId = body?.artifactId ?? String(input.artifactId);
            const selected = {
              artifactId,
              item: String(input.item),
              ordinal: Number(input.ordinal),
            };
            calls.push({
              name: request.operationName ?? "ANONYMOUS",
              hasPrivateText: text !== null,
              ...(text
                ? {
                    inputSha256: await nativeTextDigest(text),
                    journalBeforeCall: !!readInstallPlanContextJournal(
                      window.localStorage,
                      binding,
                      selected
                    ),
                  }
                : {}),
            });
            let field: string;
            if (request.operationName === "PrepareNvkeInstallPlanContext") {
              field = "prepareNvkeOperatorInstallPlanContext";
              if (!text) throw new Error("Missing controlled input");
              const saved = retained.get(artifactId);
              if (saved && saved.text !== text) {
                observer.next({
                  data: { [field]: { ok: false, errors: [{ code: "CONFLICT" }], data: null } },
                });
                observer.complete();
                return;
              }
              const data = {
                ...fixture.artifacts!,
                id: artifactId,
                item: selected.item,
                ordinal: selected.ordinal,
              };
              if (!attempted.has(artifactId)) {
                attempted.add(artifactId);
                if (retainBeforeLoss) retained.set(artifactId, { text, data });
                observer.error(new Error("Controlled lost reply"));
                return;
              }
              retained.set(artifactId, { text, data });
            } else if (request.operationName === "ReviewNvkeInstallPlanContext")
              field = "reviewNvkeOperatorInstallPlanContext";
            else if (request.operationName === "RetryNvkeInstallPlanContext")
              field = "recoverNvkeOperatorInstallPlanContextPreparation";
            else throw new Error("Unexpected controlled context operation");
            const saved = retained.get(artifactId);
            observer.next({
              data: {
                [field]: saved
                  ? { ok: true, errors: [], data: saved.data }
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
    window.__NVKE_CONTEXT_FIXTURE_CALLS__ = server.calls;
    return () => {
      if (window.__NVKE_CONTEXT_FIXTURE_CALLS__ === server.calls)
        delete window.__NVKE_CONTEXT_FIXTURE_CALLS__;
    };
  }, [server]);
  return (
    <ApolloProvider client={server.client}>
      <HookPanel />
    </ApolloProvider>
  );
}
export default {
  title: "Screens/NVKE/Controlled context recovery",
  component: ControlledRecovery,
  args: { retainBeforeLoss: false },
  parameters: { nextjs: { appDirectory: true } },
} satisfies Meta<typeof ControlledRecovery>;
type Story = StoryObj<typeof ControlledRecovery>;
export const LostBeforeRetention: Story = {};
export const LostAfterRetention: Story = { args: { retainBeforeLoss: true } };
