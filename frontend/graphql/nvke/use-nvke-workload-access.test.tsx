import {
  ApolloClient,
  ApolloLink,
  InMemoryCache,
  Observable,
  type Operation,
} from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { expect, it } from "vitest";
import messages from "@/messages/en.json";
import { SITE, OPERATION } from "@/components/screens/nvke/fixtures";
import { ACCESS_BINDING } from "@/components/screens/nvke/NvkeWorkloadAccessPanel.stories";
import type { NvkeAccessBinding } from "@/components/screens/nvke/NvkeWorkloadAccessPanel";
import { useNvkeWorkloadAccess } from "./use-nvke-workload-access";
function harness(
  mutate: (request: Operation) => Promise<NvkeAccessBinding> | NvkeAccessBinding,
  foreign = false
) {
  const calls: Operation[] = [],
    cache = new InMemoryCache();
  const client = new ApolloClient({
    cache,
    link: new ApolloLink(
      (operation) =>
        new Observable((observer) => {
          calls.push(operation);
          void Promise.resolve()
            .then(async () => {
              if (operation.operationName === "ListNvkeAccess")
                return {
                  astroliftNvkeWorkloadAccessBindings: {
                    items: foreign
                      ? [{ ...ACCESS_BINDING, siteId: OPERATION.id }]
                      : [ACCESS_BINDING],
                    nextCursor: null,
                    totalCount: 1,
                  },
                };
              if (operation.operationName === "GetNvkeResourceEditor")
                return {
                  astroliftNvkeResourceEditor: {
                    ok: true,
                    errors: [],
                    data: {
                      operationKey: "getCluster",
                      parametersText: JSON.stringify({
                        path: { clusterID: ACCESS_BINDING.clusterId },
                      }),
                      concurrency: "observed_only",
                      preparationActionKey: null,
                      source: {
                        operationId: OPERATION.id,
                        version: OPERATION.version,
                        resourceKey: null,
                      },
                    },
                  },
                };
              const value = await mutate(operation);
              return {
                [operation.operationName === "EnrollNvkeAccess"
                  ? "enrollNvkeWorkloadAccess"
                  : "revokeNvkeWorkloadAccess"]: { ok: true, errors: [], data: value },
              };
            })
            .then((data) => {
              observer.next({ data });
              observer.complete();
            })
            .catch((error) => observer.error(error));
        })
    ),
  });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
      <ApolloProvider client={client}>{children}</ApolloProvider>
    </NextIntlClientProvider>
  );
  return { calls, cache, client, wrapper };
}
function args(actor = 41, admin = true) {
  const scope = {
    server: "https://astro.example.test/app/gql/config/",
    organizationId: SITE.id,
    actorId: actor,
  };
  return {
    context: {
      scope,
      scopeKey: JSON.stringify(scope),
      access: { read: true, admin, manage: false, loading: false },
      loading: false,
      failed: false,
    },
    scopeKey: JSON.stringify(scope),
    site: SITE,
    operation: {
      ...OPERATION,
      status: "SUCCEEDED" as const,
      operationKey: "getCluster",
      isInventory: false,
    },
    enabled: true,
  };
}
it("uses saved proof and exact reviewed authority with no cache or native result download", async () => {
  const h = harness((request) =>
    request.operationName === "EnrollNvkeAccess"
      ? ACCESS_BINDING
      : { ...ACCESS_BINDING, version: 2, deletedAt: "2026-11-07T13:00:00Z" }
  );
  const { result } = renderHook(() => useNvkeWorkloadAccess(args()), { wrapper: h.wrapper });
  await waitFor(() => expect(result.current?.candidate).not.toBeNull());
  await waitFor(() => expect(result.current?.table.rows).toHaveLength(1));
  const review = result.current!.candidate!;
  await act(async () => {
    expect(await result.current!.onEnroll(review)).toBe(true);
  });
  const admission = h.calls.find((call) => call.operationName === "EnrollNvkeAccess")!;
  expect(admission.variables.input).toEqual({
    siteId: SITE.id,
    expectedSiteVersion: SITE.version,
    expectedActorId: 41,
    sourceOperationId: OPERATION.id,
    expectedSourceVersion: OPERATION.version,
  });
  await waitFor(() => expect(result.current?.table.state).toBe("ready"));
  await act(async () => {
    expect(
      await result.current!.onRevoke({
        id: ACCESS_BINDING.id,
        version: 1,
        clusterId: ACCESS_BINDING.clusterId,
        siteVersion: SITE.version,
      })
    ).toBe(true);
  });
  expect(
    h.calls.every((call) =>
      ["ListNvkeAccess", "GetNvkeResourceEditor", "EnrollNvkeAccess", "RevokeNvkeAccess"].includes(
        call.operationName ?? ""
      )
    )
  ).toBe(true);
  expect(h.cache.extract()).toEqual({});
  expect(
    h.calls.every(
      (call) =>
        call.getContext().redactDiagnostics === true &&
        call.getContext().expectedNvkeOrganization === SITE.id
    )
  ).toBe(true);
  h.client.stop();
});
it("suppresses late enrollment completion and old callbacks across A to B to A", async () => {
  let finish!: (value: NvkeAccessBinding) => void;
  const h = harness(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      })
  );
  const { result, rerender } = renderHook(({ actor }) => useNvkeWorkloadAccess(args(actor)), {
    wrapper: h.wrapper,
    initialProps: { actor: 41 },
  });
  await waitFor(() => expect(result.current?.candidate).not.toBeNull());
  const old = result.current!;
  let promise!: Promise<boolean>;
  act(() => {
    promise = old.onEnroll(old.candidate!);
  });
  await waitFor(() => expect(finish).toBeDefined());
  rerender({ actor: 42 });
  rerender({ actor: 41 });
  await waitFor(() => expect(result.current?.candidate).not.toBeNull());
  await act(async () => {
    finish(ACCESS_BINDING);
    expect(await promise).toBe(false);
  });
  expect(result.current?.enrolled).toBe(false);
  await act(async () => {
    expect(await old.onEnroll(old.candidate!)).toBe(false);
  });
  expect(h.calls.filter((call) => call.operationName === "EnrollNvkeAccess")).toHaveLength(1);
  h.client.stop();
});
it("rejects foreign pages and sanitizes unconfirmed enrollment failures", async () => {
  const h = harness(() => {
    throw new Error("PRIVATE_NATIVE_ERROR");
  }, true);
  const { result } = renderHook(() => useNvkeWorkloadAccess(args()), { wrapper: h.wrapper });
  await waitFor(() => expect(result.current?.table.state).toBe("error"));
  expect(result.current?.table.rows).toEqual([]);
  await waitFor(() => expect(result.current?.candidate).not.toBeNull());
  await act(async () => {
    expect(await result.current!.onEnroll(result.current!.candidate!)).toBe(false);
  });
  expect(result.current?.error).toBe(messages.nvke.workloadAccess.writeFailed);
  expect(JSON.stringify(result.current)).not.toContain("PRIVATE");
  h.client.stop();
});
it("never prepares or admits enrollment for a read-only caller", async () => {
  const h = harness(() => ACCESS_BINDING);
  const { result } = renderHook(() => useNvkeWorkloadAccess(args(41, false)), {
    wrapper: h.wrapper,
  });
  await waitFor(() => expect(result.current?.table.rows).toHaveLength(1));
  expect(result.current?.candidate).toBeNull();
  expect(result.current?.canManage).toBe(false);
  expect(h.calls.map((call) => call.operationName)).toEqual(["ListNvkeAccess"]);
  h.client.stop();
});

it("does not read or render grants while caller authority is unavailable", async () => {
  const h = harness(() => ACCESS_BINDING);
  const a = args();
  const { result } = renderHook(
    () => useNvkeWorkloadAccess({ ...a, context: { ...a.context, scope: null, scopeKey: "" } }),
    { wrapper: h.wrapper }
  );
  await act(async () => {});
  expect(result.current).toBeNull();
  expect(h.calls).toEqual([]);
  h.client.stop();
});
