import { useEffect, useMemo } from "react";
import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { useNvkeInstallPlanVerification } from "@/graphql/nvke/use-nvke-install-plan-verification";
import { readInstallPlanVerificationJournal } from "@/lib/nvke/install-plan-verification-recovery";
import { nativeTextDigest } from "@/lib/nvke/recovery";
import { NvkeOperatorInstallPlanVerificationPanel } from "./NvkeOperatorInstallPlanVerificationPanel";
import { INSTALL_VERIFICATION_PROPS as fixture } from "./NvkeOperatorInstallPlanVerificationPanel.stories";
import { INSTALL_CONTEXT_PROPS as SOURCE_CONTEXT_PROPS } from "./NvkeOperatorInstallPlanContextPanel.stories";
const plan = fixture.plan!;
const context = {
  scope: { server: "http://127.0.0.1/fixture-graphql/", organizationId: plan.siteId, actorId: 41 },
  scopeKey: "controlled-verification-recovery",
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
  contextId: fixture.context!.id,
  contextVersion: fixture.context!.version,
  contextDigest: fixture.context!.contextDigest,
  item: fixture.context!.item,
  ordinal: fixture.context!.ordinal,
};
declare global {
  interface Window {
    __NVKE_VERIFICATION_FIXTURE_CALLS__?: {
      name: string;
      hasPrivateText: boolean;
      inputSha256?: string;
      journalBeforeCall?: boolean;
    }[];
  }
}
function HookPanel() {
  const panel = useNvkeInstallPlanVerification({
    context,
    source: SOURCE_CONTEXT_PROPS,
    pending: false,
  });
  return panel ? <NvkeOperatorInstallPlanVerificationPanel {...panel} /> : null;
}
function createControlledClient(retainBeforeLoss: boolean) {
  const attempted = new Set<string>();
  const retained = new Map<string, { text: string; data: NonNullable<typeof fixture.artifacts> }>();
  const calls: NonNullable<Window["__NVKE_VERIFICATION_FIXTURE_CALLS__"]> = [];
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
            const selected = { artifactId };
            calls.push({
              name: request.operationName ?? "ANONYMOUS",
              hasPrivateText: text !== null,
              ...(text
                ? {
                    inputSha256: await nativeTextDigest(text),
                    journalBeforeCall: !!readInstallPlanVerificationJournal(
                      window.localStorage,
                      binding,
                      selected
                    ),
                  }
                : {}),
            });
            let field: string;
            if (request.operationName === "PrepareNvkeInstallPlanVerification") {
              field = "prepareNvkeOperatorInstallPlanVerification";
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
              };
              if (!attempted.has(artifactId)) {
                attempted.add(artifactId);
                if (retainBeforeLoss) retained.set(artifactId, { text, data });
                observer.error(new Error("Controlled lost reply"));
                return;
              }
              retained.set(artifactId, { text, data });
            } else if (request.operationName === "ReviewNvkeInstallPlanVerification")
              field = "reviewNvkeOperatorInstallPlanVerification";
            else if (request.operationName === "RetryNvkeInstallPlanVerification")
              field = "recoverNvkeOperatorInstallPlanVerificationPreparation";
            else throw new Error("Unexpected controlled verification operation");
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
    window.__NVKE_VERIFICATION_FIXTURE_CALLS__ = server.calls;
    return () => {
      if (window.__NVKE_VERIFICATION_FIXTURE_CALLS__ === server.calls)
        delete window.__NVKE_VERIFICATION_FIXTURE_CALLS__;
    };
  }, [server]);
  return (
    <ApolloProvider client={server.client}>
      <HookPanel />
    </ApolloProvider>
  );
}
export default {
  title: "Screens/NVKE/Controlled verification recovery",
  component: ControlledRecovery,
  args: { retainBeforeLoss: false },
  parameters: { nextjs: { appDirectory: true } },
} satisfies Meta<typeof ControlledRecovery>;
type Story = StoryObj<typeof ControlledRecovery>;
export const LostBeforeRetention: Story = {};
export const LostAfterRetention: Story = { args: { retainBeforeLoss: true } };
