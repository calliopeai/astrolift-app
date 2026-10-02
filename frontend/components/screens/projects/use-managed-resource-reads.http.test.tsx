import { createServer, type ServerResponse } from "node:http";
import { ApolloClient, ApolloLink, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { PropsWithChildren } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import { ATTACHMENT, RESOURCE } from "./resource-reads.fixtures";
import { useManagedResourceReads } from "./use-managed-resource-reads";

const identity = vi.hoisted(() => ({
  orgId: "01930000-0000-7000-8000-000000000002",
  userId: "actor-a",
}));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: identity.orgId ? { id: identity.orgId } : null }),
}));
vi.mock("@/graphql/user/user.hooks", () => ({
  useMe: () => ({ user: identity.userId ? { id: identity.userId } : null }),
}));
vi.mock("@/hooks/use-confirm", () => ({ useConfirm: () => async () => true }));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

type Request = {
  name: string;
  variables: Record<string, unknown>;
  query: string;
  actor: string;
  org: string;
};
const cleanups: Array<() => Promise<void>> = [];
beforeEach(() => {
  identity.orgId = RESOURCE.organizationId;
  identity.userId = "actor-a";
});
afterEach(async () => {
  cleanup();
  for (const close of cleanups.splice(0)) await close();
});

async function fixture() {
  const requests: Request[] = [];
  const pending: Array<{ request: Request; finish: () => void }> = [];
  const held = new Set<string>();
  const denied = new Set<string>();
  const deniedOrgs = new Set<string>();
  const state = { stalePage: false, replacement: false };
  const rows = Array.from({ length: 251 }, (_, index) => ({
    ...RESOURCE,
    id: index === 0 ? RESOURCE.id : `01940000-0000-7000-8000-${String(index).padStart(12, "0")}`,
    name: `resource-${index}`,
    __typename: "AstroliftManagedServiceContext",
  }));
  const consumers = Array.from({ length: 251 }, (_, index) => ({
    ...ATTACHMENT,
    id: `01950000-0000-7000-8000-${String(index).padStart(12, "0")}`,
    consumerSlug: `consumer-${index}`,
    __typename: "AstroliftManagedServiceAttachmentContext",
  }));
  function body(request: Request) {
    if (denied.has(`${request.name}:${request.actor}`) || deniedOrgs.has(request.org))
      return { errors: [{ message: "Read refused", extensions: { code: "FORBIDDEN" } }] };
    const variables = request.variables;
    if (
      request.name === "ListProjectResourcePage" ||
      request.name === "ListProjectResourceAttachmentsPage"
    ) {
      if (state.stalePage && variables.after)
        return { errors: [{ message: "Restart the page", extensions: { code: "STALE_CURSOR" } }] };
      const all =
        request.name === "ListProjectResourcePage"
          ? rows
          : consumers.map((row) => ({ ...row, managedServiceId: variables.managedServiceId }));
      const start = variables.after ? Number(String(variables.after).split(":")[1]) : 0;
      const limit = Number(variables.limit ?? 25);
      const end = Math.min(start + limit, all.length);
      const field =
        request.name === "ListProjectResourcePage"
          ? "astroliftProjectManagedServicesPage"
          : "astroliftProjectManagedServiceAttachmentsPage";
      return {
        data: {
          [field]: {
            items: all.slice(start, end),
            totalCount: all.length,
            nextCursor: end < all.length ? `server:${end}` : null,
          },
        },
      };
    }
    if (request.name === "GetProjectResource")
      return {
        data: {
          astroliftProjectManagedService: state.replacement
            ? null
            : (rows.find((row) => row.id === variables.id) ?? null),
        },
      };
    if (request.name === "PreviewManagedServiceCost")
      return {
        data: {
          astroliftManagedServiceCostPreview: {
            managedServiceId: variables.managedServiceId,
            available: true,
            reason: "",
            message: "",
            monthlyTotal: 42.5,
            currency: "USD",
            pricingSourceUrl: "https://prices.example.test/redis",
            pricingFetchedAt: "2026-10-02T00:00:00Z",
            notes: [],
            approximate: false,
            lineItems: [],
          },
        },
      };
    const fields: Record<string, string> = {
      ReprovisionProjectManagedService: "reprovisionProjectManagedService",
      DeprovisionProjectManagedService: "deprovisionProjectManagedService",
      AttachProjectManagedService: "attachProjectManagedService",
      DetachProjectManagedService: "detachProjectManagedService",
    };
    return { data: { [fields[request.name]]: { ok: true, errors: [], data: null } } };
  }
  const responses = new Set<ServerResponse>();
  const server = createServer((req, res) => {
    responses.add(res);
    let raw = "";
    req.on("data", (chunk) => {
      raw += chunk;
    });
    req.on("end", () => {
      const parsed = JSON.parse(raw);
      const request: Request = {
        name: parsed.operationName,
        variables: parsed.variables,
        query: parsed.query,
        actor: String(req.headers["x-test-actor"] ?? ""),
        org: String(req.headers["x-astrolift-organization"] ?? ""),
      };
      requests.push(request);
      const payload = body(request);
      const finish = () => {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify(payload));
        responses.delete(res);
      };
      if (held.has(`${request.name}:${request.actor}`)) pending.push({ request, finish });
      else finish();
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("fixture address");
  const identityLink = new ApolloLink((operation, forward) => {
    operation.setContext({
      headers: { "X-Test-Actor": identity.userId, "X-Astrolift-Organization": identity.orgId },
    });
    return forward(operation);
  });
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: identityLink.concat(
      new HttpLink({ uri: `http://127.0.0.1:${address.port}/graphql`, fetch })
    ),
  });
  cleanups.push(async () => {
    client.stop();
    for (const response of responses) response.end();
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });
  const wrapper = ({ children }: PropsWithChildren) => (
    <ApolloProvider client={client}>
      <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
        {children}
      </NextIntlClientProvider>
    </ApolloProvider>
  );
  return { requests, pending, held, denied, deniedOrgs, state, rows, wrapper };
}

async function openFirst(result: { current: ReturnType<typeof useManagedResourceReads> }) {
  await waitFor(() => expect(result.current.resourceTable.state).toBe("ready"));
  act(() => result.current.onOpenResource(result.current.resourceTable.rows[0]));
  await waitFor(() => expect(result.current.resourceDetail.current?.id).toBe(RESOURCE.id));
  await waitFor(() => expect(result.current.resourceDetail.attachments.state).toBe("ready"));
}

describe("actor-bound managed resource HTTP reads", () => {
  it("walks all 251 resources and consumers using only server cursors and metadata", async () => {
    const api = await fixture();
    const { result } = renderHook(() => useManagedResourceReads(RESOURCE.projectId!, true, true), {
      wrapper: api.wrapper,
    });
    const seen = new Set<string>();
    await waitFor(() => expect(result.current.resourceTable.state).toBe("ready"));
    for (;;) {
      for (const row of result.current.resourceTable.rows) {
        expect(seen.has(row.id)).toBe(false);
        seen.add(row.id);
      }
      expect(result.current.resourceTable.rows.length).toBeLessThanOrEqual(25);
      if (!result.current.resourceTable.hasNext) break;
      const first = result.current.resourceTable.rows[0].id;
      act(() => result.current.resourceTable.next());
      await waitFor(() => {
        expect(result.current.resourceTable.isStale).toBe(false);
        expect(result.current.resourceTable.rows[0].id).not.toBe(first);
      });
    }
    expect(seen.size).toBe(251);
    act(() => result.current.resourceTable.prev());
    await waitFor(() => expect(result.current.resourceTable.isStale).toBe(false));
    act(() => result.current.onOpenResource(result.current.resourceTable.rows[0]));
    await waitFor(() => expect(result.current.resourceDetail.current).not.toBeNull());
    await waitFor(() => expect(result.current.resourceDetail.attachments.state).toBe("ready"));
    const consumers = new Set<string>();
    for (;;) {
      const table = result.current.resourceDetail.attachments;
      for (const row of table.rows) {
        expect(consumers.has(row.id)).toBe(false);
        consumers.add(row.id);
      }
      if (!table.hasNext) break;
      const first = table.rows[0].id;
      act(() => table.next());
      await waitFor(() => {
        expect(result.current.resourceDetail.attachments.isStale).toBe(false);
        expect(result.current.resourceDetail.attachments.rows[0].id).not.toBe(first);
      });
    }
    expect(consumers.size).toBe(251);
    expect(
      api.requests.every(
        (request) =>
          !/\b(config|appliedConfig|statusError|volumeBindings|backendRef|iamGrants)\b/.test(
            request.query
          )
      )
    ).toBe(true);
    expect(api.requests.some((request) => request.name === "PreviewManagedServiceCost")).toBe(
      false
    );
    expect(
      api.requests
        .filter((request) => request.name === "ListProjectResourceAttachmentsPage")
        .every((request) => request.variables.expectedContextRevision === RESOURCE.contextRevision)
    ).toBe(true);
  });
  it("shows stale continuation as refusal and retries from the first server page", async () => {
    const api = await fixture();
    const { result } = renderHook(() => useManagedResourceReads(RESOURCE.projectId!, true, true), {
      wrapper: api.wrapper,
    });
    await waitFor(() => expect(result.current.resourceTable.state).toBe("ready"));
    api.state.stalePage = true;
    act(() => result.current.resourceTable.next());
    await waitFor(() => expect(result.current.resourceTable.state).toBe("error"));
    act(() => result.current.resourceTable.retry());
    await waitFor(() => expect(result.current.resourceTable.state).toBe("ready"));
    expect(result.current.resourceTable.pageIndex).toBe(0);
    expect(api.requests.at(-1)?.variables.after).toBeUndefined();
  });
  it("does not deduplicate a new actor's identical detail query with the prior actor's in-flight read", async () => {
    const api = await fixture();
    api.held.add("GetProjectResource:actor-a");
    api.denied.add("GetProjectResource:actor-b");
    const { result, rerender } = renderHook(
      () => useManagedResourceReads(RESOURCE.projectId!, true, true),
      { wrapper: api.wrapper }
    );
    await waitFor(() => expect(result.current.resourceTable.state).toBe("ready"));
    act(() => result.current.onOpenResource(result.current.resourceTable.rows[0]));
    await waitFor(() => expect(api.pending.length).toBe(1));
    identity.userId = "actor-b";
    rerender();
    expect(result.current.resourceDetail.target).toBeNull();
    expect(result.current.resourceTable.rows).toEqual([]);
    await waitFor(() => expect(result.current.resourceTable.state).toBe("ready"));
    act(() => result.current.onOpenResource(result.current.resourceTable.rows[0]));
    await waitFor(() => expect(result.current.resourceDetail.refused).toBe(true));
    expect(
      api.requests
        .filter((request) => request.name === "GetProjectResource")
        .map((request) => request.actor)
    ).toEqual(["actor-a", "actor-b"]);
    act(() => api.pending[0].finish());
    await new Promise((resolve) => setTimeout(resolve, 25));
    expect(result.current.resourceDetail.current).toBeNull();
    expect(result.current.resourceDetail.refused).toBe(true);
  });
  it("discards late price and old write/review handlers after an actor switch", async () => {
    const api = await fixture();
    const { result, rerender } = renderHook(
      () => useManagedResourceReads(RESOURCE.projectId!, true, true),
      { wrapper: api.wrapper }
    );
    await openFirst(result);
    const oldWrite = result.current.resourceDetail.onReprovision;
    const oldReview = result.current.resourceDetail.onRefresh;
    api.held.add("PreviewManagedServiceCost:actor-a");
    act(() => {
      void result.current.resourceDetail.onCost();
    });
    await waitFor(() => expect(api.pending.length).toBe(1));
    identity.userId = "actor-b";
    rerender();
    await openFirst(result);
    await act(async () => {
      await oldWrite();
      await oldReview();
    });
    expect(
      api.requests.some((request) => request.name === "ReprovisionProjectManagedService")
    ).toBe(false);
    api.denied.add("PreviewManagedServiceCost:actor-b");
    act(() => {
      void result.current.resourceDetail.onCost();
    });
    await waitFor(() => expect(result.current.resourceDetail.costError).toBe(true));
    expect(
      api.requests
        .filter((request) => request.name === "PreviewManagedServiceCost")
        .map((request) => request.actor)
    ).toEqual(["actor-a", "actor-b"]);
    act(() => api.pending[0].finish());
    await new Promise((resolve) => setTimeout(resolve, 25));
    expect(result.current.resourceDetail.cost).toBeUndefined();
    expect(result.current.resourceDetail.costError).toBe(true);
  });
  it("pins action inputs to the independently observed exact context", async () => {
    const api = await fixture();
    const { result } = renderHook(() => useManagedResourceReads(RESOURCE.projectId!, true, true), {
      wrapper: api.wrapper,
    });
    await openFirst(result);
    await act(async () => {
      await result.current.resourceDetail.onDetach(
        result.current.resourceDetail.attachments.rows[0].id
      );
    });
    expect(
      api.requests.find((request) => request.name === "DetachProjectManagedService")?.variables
        .input
    ).toMatchObject({
      managedServiceId: RESOURCE.id,
      expectedContextRevision: RESOURCE.contextRevision,
    });
    await waitFor(() => expect(result.current.resourceDetail.attachments.isStale).toBe(false));
    await act(async () => {
      await result.current.resourceDetail.onReprovision();
    });
    expect(
      api.requests.find((request) => request.name === "ReprovisionProjectManagedService")?.variables
        .input
    ).toEqual({ managedServiceId: RESOURCE.id, expectedContextRevision: RESOURCE.contextRevision });
    expect(result.current.resourceDetail.target).toBeNull();
  });
  it("starts a distinct org-bound read and refuses its predecessor's late response", async () => {
    const api = await fixture();
    api.held.add("ListProjectResourcePage:actor-a");
    const { result, rerender } = renderHook(
      () => useManagedResourceReads(RESOURCE.projectId!, true, true),
      { wrapper: api.wrapper }
    );
    await waitFor(() => expect(api.pending.length).toBe(1));
    api.held.clear();
    identity.orgId = "01930000-0000-7000-8000-000000000099";
    api.deniedOrgs.add(identity.orgId);
    rerender();
    await waitFor(() => expect(result.current.resourceTable.state).toBe("error"));
    expect(api.requests.map((request) => request.org)).toEqual([
      RESOURCE.organizationId,
      identity.orgId,
    ]);
    act(() => api.pending[0].finish());
    await new Promise((resolve) => setTimeout(resolve, 25));
    expect(result.current.resourceTable.rows).toEqual([]);
    expect(result.current.resourceTable.state).toBe("error");
  });
  it("does not reuse an old selected-target price after an A/B/A selection", async () => {
    const api = await fixture();
    const { result } = renderHook(() => useManagedResourceReads(RESOURCE.projectId!, true, true), {
      wrapper: api.wrapper,
    });
    await openFirst(result);
    api.held.add("PreviewManagedServiceCost:actor-a");
    act(() => {
      void result.current.resourceDetail.onCost();
    });
    await waitFor(() => expect(api.pending.length).toBe(1));
    act(() => result.current.onOpenResource(result.current.resourceTable.rows[1]));
    await waitFor(() => expect(result.current.resourceDetail.current?.id).toBe(api.rows[1].id));
    act(() => result.current.onOpenResource(result.current.resourceTable.rows[0]));
    await waitFor(() => expect(result.current.resourceDetail.current?.id).toBe(RESOURCE.id));
    expect(result.current.resourceDetail.costLoading).toBe(false);
    act(() => api.pending[0].finish());
    await new Promise((resolve) => setTimeout(resolve, 25));
    expect(result.current.resourceDetail.cost).toBeUndefined();
  });
  it("never rebinds a deleted target to a replacement or dispatches from a refused review", async () => {
    const api = await fixture();
    const { result } = renderHook(() => useManagedResourceReads(RESOURCE.projectId!, true, true), {
      wrapper: api.wrapper,
    });
    await openFirst(result);
    api.state.replacement = true;
    await act(async () => {
      await result.current.resourceDetail.onRefresh();
    });
    await waitFor(() => expect(result.current.resourceDetail.refused).toBe(true));
    await act(async () => {
      await result.current.resourceDetail.onReprovision();
    });
    expect(
      api.requests
        .filter((request) => request.name === "GetProjectResource")
        .every((request) => request.variables.id === RESOURCE.id)
    ).toBe(true);
    expect(
      api.requests.some((request) => request.name === "ReprovisionProjectManagedService")
    ).toBe(false);
  });
  it("keeps read-only triage independent of price and write permissions", async () => {
    const api = await fixture();
    const { result } = renderHook(
      () => useManagedResourceReads(RESOURCE.projectId!, false, false),
      { wrapper: api.wrapper }
    );
    await openFirst(result);
    await act(async () => {
      await result.current.resourceDetail.onCost();
      await result.current.resourceDetail.onReprovision();
    });
    expect(new Set(api.requests.map((request) => request.name))).toEqual(
      new Set([
        "ListProjectResourcePage",
        "GetProjectResource",
        "ListProjectResourceAttachmentsPage",
      ])
    );
  });
});
