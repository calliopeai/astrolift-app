import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { OperationDefinitionNode } from "graphql";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { DEPLOY_RUNNING } from "@/components/screens/apps/deployments/app-deployments-logs.fixtures";

import { useDeploymentDetail } from "./use-deployment-detail";

const spies = vi.hoisted(() => ({ download: vi.fn(), error: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: spies.error } }));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true }),
}));
vi.mock("./run-support", async (original) => ({
  ...(await original<typeof import("./run-support")>()),
  downloadText: spies.download,
}));

const line = (id: string) => ({
  id,
  deploymentId: DEPLOY_RUNNING.id,
  status: "",
  phase: "build",
  event: "output",
  message: id,
  detail: {},
  occurredAt: "2026-09-29T00:00:00Z",
});

function setup() {
  const requests: { name: string; variables: Record<string, unknown> }[] = [];
  let snapshot = 1;
  let resolveOlder: (() => void) | undefined;
  let rejectOlder: (() => void) | undefined;
  const link = new ApolloLink(
    (operation) =>
      new Observable((observer) => {
        requests.push({ name: operation.operationName ?? "", variables: operation.variables });
        const definition = operation.query.definitions.find(
          (d) => d.kind === "OperationDefinition"
        ) as OperationDefinitionNode;
        if (definition.operation === "subscription") return;
        const data: Record<string, unknown> = {};
        for (const selection of definition.selectionSet.selections) {
          if (selection.kind !== "Field") continue;
          const name = selection.name.value;
          data[name] =
            name === "astroliftDeployment"
              ? DEPLOY_RUNNING
              : name === "astroliftEvents" || name === "astroliftDeploymentApprovalHistory"
                ? []
                : null;
          if (name === "astroliftDeploymentRunLogPage") {
            data[name] = operation.variables.cursor
              ? { items: [line("older")], nextCursor: null, hasMore: false, pageSize: 100 }
              : {
                  items: [line(`latest-${snapshot}`)],
                  nextCursor: `cursor-${snapshot}`,
                  hasMore: true,
                  pageSize: 100,
                };
          }
          if (name === "astroliftDeploymentRunLogDownload")
            data[name] = {
              filename: "complete.log",
              content: "all persisted history beyond this page",
              contentType: "text/plain; charset=utf-8",
            };
        }
        const deliver = () => {
          observer.next({ data });
          observer.complete();
        };
        if (operation.variables.cursor) {
          resolveOlder = deliver;
          rejectOlder = () => observer.error(new Error("Older page denied"));
        } else setTimeout(deliver, 0);
      })
  );
  const client = new ApolloClient({ link, cache: new InMemoryCache() });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <ApolloProvider client={client}>{children}</ApolloProvider>
  );
  const hook = renderHook(() => useDeploymentDetail(DEPLOY_RUNNING.id), { wrapper });
  return {
    ...hook,
    requests,
    refresh: () => {
      snapshot += 1;
    },
    finishOlder: () => resolveOlder?.(),
    failOlder: () => rejectOlder?.(),
  };
}

async function loaded(test: ReturnType<typeof setup>, message = "latest-1") {
  await waitFor(() => expect(test.result.current.log.map((e) => e.message)).toEqual([message]));
}

describe("persisted deployment run log through Apollo", () => {
  it("loads older entries in chronological order and downloads the complete server artifact", async () => {
    const test = setup();
    await loaded(test);
    let loading: Promise<void>;
    await act(async () => {
      loading = test.result.current.onLoadOlderLog();
    });
    await act(async () => {
      test.finishOlder();
      await loading!;
    });
    await waitFor(() =>
      expect(test.result.current.log.map((e) => e.message)).toEqual(["older", "latest-1"])
    );
    expect(test.result.current.hasOlderLog).toBe(false);
    await act(() => test.result.current.onDownload());
    expect(spies.download).toHaveBeenCalledWith(
      "complete.log",
      "all persisted history beyond this page"
    );
    expect(
      test.requests.find((r) => r.name === "GetDeploymentRunLogPage" && r.variables.cursor)
        ?.variables
    ).toMatchObject({ deploymentId: DEPLOY_RUNNING.id, cursor: "cursor-1" });
  });

  it("ignores an older response after a refresh changes the snapshot", async () => {
    const test = setup();
    await loaded(test);
    let loading: Promise<void>;
    await act(async () => {
      loading = test.result.current.onLoadOlderLog();
    });
    test.refresh();
    await act(() => test.result.current.onRetryLog());
    await loaded(test, "latest-2");
    await act(async () => {
      test.finishOlder();
      await loading!;
    });
    expect(test.result.current.log.map((e) => e.message)).toEqual(["latest-2"]);
    expect(test.result.current.hasOlderLog).toBe(true);
  });

  it("keeps the current page and surfaces a denied older page", async () => {
    const test = setup();
    await loaded(test);
    let loading: Promise<void>;
    await act(async () => {
      loading = test.result.current.onLoadOlderLog();
    });
    await act(async () => {
      test.failOlder();
      await loading!;
    });
    expect(test.result.current.log.map((e) => e.message)).toEqual(["latest-1"]);
    expect(spies.error).toHaveBeenCalledWith("Older page denied");
  });
});
