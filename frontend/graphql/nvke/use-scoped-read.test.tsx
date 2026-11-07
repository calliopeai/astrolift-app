import { ApolloClient, ApolloLink, gql, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useNvkeRead } from "./use-scoped-read";

const query = gql`
  query NvkeReadTest($id: String!) {
    value(id: $id)
  }
`;
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
function harness() {
  const pending: {
    reply: (value: string) => void;
    fail: () => void;
    context: Record<string, unknown>;
  }[] = [];
  const cache = new InMemoryCache();
  const client = new ApolloClient({
    cache,
    link: new ApolloLink(
      (operation) =>
        new Observable((observer) => {
          pending.push({
            reply: (value) => {
              observer.next({ data: { value } });
              observer.complete();
            },
            fail: () => observer.error(new Error("private response")),
            context: operation.getContext(),
          });
        })
    ),
  });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <ApolloProvider client={client}>{children}</ApolloProvider>
  );
  return { pending, cache, client, wrapper };
}
it("ignores late responses across caller changes, including returning to the first caller", async () => {
  const h = harness();
  const { result, rerender } = renderHook(
    ({ scope }) => useNvkeRead<{ value: string }>(query, { id: "site" }, scope),
    { wrapper: h.wrapper, initialProps: { scope: "actor-A" } }
  );
  await waitFor(() => expect(h.pending).toHaveLength(1));
  rerender({ scope: "actor-B" });
  await waitFor(() => expect(h.pending).toHaveLength(2));
  rerender({ scope: "actor-A" });
  await waitFor(() => expect(h.pending).toHaveLength(3));
  await act(async () => {
    h.pending[0].reply("old-A");
    h.pending[1].reply("old-B");
  });
  expect(result.current.data).toBeUndefined();
  expect(result.current.loading).toBe(true);
  await act(async () => h.pending[2].reply("current-A"));
  expect(result.current.data?.value).toBe("current-A");
  expect(h.cache.extract()).toEqual({});
  expect(
    h.pending.every(
      (row) => row.context.queryDeduplication === false && row.context.redactDiagnostics === true
    )
  ).toBe(true);
  h.client.stop();
});
it("clears previous data during refresh and exposes only a safe failure flag", async () => {
  const h = harness();
  const { result } = renderHook(
    () => useNvkeRead<{ value: string }>(query, { id: "site" }, "actor-A"),
    { wrapper: h.wrapper }
  );
  await waitFor(() => expect(h.pending).toHaveLength(1));
  await act(async () => h.pending[0].reply("value"));
  act(() => result.current.refresh());
  expect(result.current.data).toBeUndefined();
  await waitFor(() => expect(h.pending).toHaveLength(2));
  await act(async () => h.pending[1].fail());
  expect(result.current).toMatchObject({ data: undefined, failed: true, loading: false });
  expect(JSON.stringify(result.current)).not.toContain("private");
  h.client.stop();
});
it("does not read without a verified caller scope", async () => {
  const h = harness();
  const { result } = renderHook(() => useNvkeRead(query, { id: "site" }, ""), {
    wrapper: h.wrapper,
  });
  await act(async () => {});
  expect(h.pending).toHaveLength(0);
  expect(result.current).toMatchObject({ data: undefined, failed: false, loading: false });
  h.client.stop();
});
