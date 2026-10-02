import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PREVIEWS } from "./previews.fixtures";
import { usePreviewDetail } from "./use-preview-detail";

const identity = vi.hoisted(() => ({ orgId: "org-one", userId: "actor-one" }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: identity.orgId ? { id: identity.orgId } : null }),
}));
vi.mock("@/graphql/user/user.hooks", () => ({
  useMe: () => ({ user: identity.userId ? { id: identity.userId } : null }),
}));

type Request = { name: string; variables: Record<string, unknown>; actor: string };
function setup(hold = false) {
  const requests: Request[] = [];
  const pending: Array<() => void> = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink(
      (operation) =>
        new Observable((observer) => {
          requests.push({
            name: operation.operationName ?? "",
            variables: operation.variables,
            actor: identity.userId,
          });
          const id = operation.variables.id ?? PREVIEWS[0].id;
          const data =
            operation.operationName === "GetPreviewEnvironment"
              ? {
                  astroliftPreviewEnvironment: {
                    ...PREVIEWS[0],
                    id,
                    environment: { ...PREVIEWS[0].environment, previewId: id },
                    runtimeStatus: operation.variables.includeRuntimeCost
                      ? "available"
                      : "not_requested",
                  },
                }
              : operation.operationName === "GetPreviewLogs"
                ? { astroliftAppLogs: { reason: "OK", historicalAvailable: true, items: [] } }
                : {
                    astroliftPreviewDeploymentsPage: { items: [], nextCursor: null, totalCount: 0 },
                  };
          const finish = () => {
            observer.next({ data });
            observer.complete();
          };
          if (hold) pending.push(finish);
          else finish();
        })
    ),
  });
  const wrapper = ({ children }: PropsWithChildren) => (
    <ApolloProvider client={client}>{children}</ApolloProvider>
  );
  return { requests, pending, wrapper };
}

beforeEach(() => {
  identity.orgId = "org-one";
  identity.userId = "actor-one";
});

describe("exact preview detail", () => {
  it("loads one GUID without walking catalog or requesting runtime pricing/logs", async () => {
    const fixture = setup();
    const { result } = renderHook(() => usePreviewDetail(PREVIEWS[0].id), {
      wrapper: fixture.wrapper,
    });
    await waitFor(() => expect(result.current.preview?.id).toBe(PREVIEWS[0].id));
    expect(fixture.requests).toEqual([
      {
        name: "GetPreviewEnvironment",
        variables: { id: PREVIEWS[0].id, includeRuntimeCost: false },
        actor: "actor-one",
      },
    ]);
    expect(result.current.preview?.runtimeStatus).toBe("not_requested");
    expect(result.current.logsRequested).toBe(false);
  });

  it("runtime data is requested only by the explicit control", async () => {
    const fixture = setup();
    const { result } = renderHook(() => usePreviewDetail(PREVIEWS[0].id), {
      wrapper: fixture.wrapper,
    });
    await waitFor(() => expect(result.current.preview).not.toBeNull());
    act(() => result.current.onLoadRuntime());
    await waitFor(() => expect(result.current.preview?.runtimeStatus).toBe("available"));
    expect(fixture.requests[1].variables).toEqual({ id: PREVIEWS[0].id, includeRuntimeCost: true });
  });

  it("log and deployment reads carry the reviewed GUID and immutable versions", async () => {
    const fixture = setup();
    const { result } = renderHook(() => usePreviewDetail(PREVIEWS[0].id), {
      wrapper: fixture.wrapper,
    });
    await waitFor(() => expect(result.current.preview).not.toBeNull());
    act(() => result.current.onLoadLogs());
    await waitFor(() => expect(result.current.logsRequested).toBe(true));
    act(() => result.current.onLoadDeployments());
    await waitFor(() => expect(result.current.deploymentsRequested).toBe(true));
    const proof = {
      expectedEnvironmentId: PREVIEWS[0].environment!.environmentId,
      ifMatchPreviewVersion: 1,
      ifMatchEnvironmentVersion: 1,
    };
    expect(
      fixture.requests.find((request) => request.name === "GetPreviewLogs")?.variables
    ).toMatchObject({ appSlug: "checkout-api", previewId: PREVIEWS[0].id, ...proof, limit: 200 });
    expect(
      fixture.requests.find((request) => request.name === "GetPreviewDeploymentsPage")?.variables
    ).toEqual({ id: PREVIEWS[0].id, ...proof, limit: 20, after: null });
  });

  it("switching preview discards the old detail and resets runtime opt-in", async () => {
    const fixture = setup();
    const { result, rerender } = renderHook(({ id }) => usePreviewDetail(id), {
      wrapper: fixture.wrapper,
      initialProps: { id: PREVIEWS[0].id },
    });
    await waitFor(() => expect(result.current.preview).not.toBeNull());
    act(() => result.current.onLoadRuntime());
    await waitFor(() => expect(result.current.preview?.runtimeStatus).toBe("available"));
    rerender({ id: PREVIEWS[1].id });
    await waitFor(() => expect(result.current.preview?.id).toBe(PREVIEWS[1].id));
    expect(fixture.requests.at(-1)?.variables).toEqual({
      id: PREVIEWS[1].id,
      includeRuntimeCost: false,
    });
    expect(result.current.logs).toBeNull();
  });

  it("late metadata from the previous actor cannot populate the current detail", async () => {
    const fixture = setup(true);
    const { result, rerender } = renderHook(() => usePreviewDetail(PREVIEWS[0].id), {
      wrapper: fixture.wrapper,
    });
    await waitFor(() => expect(fixture.pending).toHaveLength(1));
    identity.userId = "actor-two";
    rerender();
    await waitFor(() => expect(fixture.pending).toHaveLength(2));
    act(() => fixture.pending[0]());
    expect(result.current.preview).toBeNull();
    act(() => fixture.pending[1]());
    await waitFor(() => expect(result.current.preview?.id).toBe(PREVIEWS[0].id));
    expect(fixture.requests.map((request) => request.actor)).toEqual(["actor-one", "actor-two"]);
  });
});
