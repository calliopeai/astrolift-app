import { ApolloClient, ApolloLink, gql, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { expect, it } from "vitest";
import { useScopedCursorTable } from "./use-scoped-cursor-table";

const query = gql`
  query ScopedPages($after: String, $limit: Int) {
    page(after: $after, limit: $limit) {
      items {
        id
      }
      nextCursor
      totalCount
    }
  }
`;
it("ignores abandoned page reads when a caller switches away and returns", async () => {
  const replies: ((id: string) => void)[] = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink(
      () =>
        new Observable((observer) => {
          replies.push((id) => {
            observer.next({ data: { page: { items: [{ id }], nextCursor: null, totalCount: 1 } } });
            observer.complete();
          });
        })
    ),
  });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <ApolloProvider client={client}>{children}</ApolloProvider>
  );
  const { result, rerender } = renderHook(
    ({ scope }) =>
      useScopedCursorTable<{ id: string }>(
        {
          query,
          extract: (data) =>
            (
              data as
                | { page: { items: { id: string }[]; nextCursor: null; totalCount: number } }
                | undefined
            )?.page,
        },
        scope
      ),
    { wrapper, initialProps: { scope: "A" } }
  );
  await waitFor(() => expect(replies).toHaveLength(1));
  rerender({ scope: "B" });
  await waitFor(() => expect(replies).toHaveLength(2));
  rerender({ scope: "A" });
  await waitFor(() => expect(replies).toHaveLength(3));
  await act(async () => {
    replies[0]("abandoned-A");
    replies[1]("abandoned-B");
  });
  expect(result.current.rows).toEqual([]);
  await act(async () => replies[2]("current-A"));
  await waitFor(() => expect(result.current.rows).toEqual([{ id: "current-A" }]));
  client.stop();
});
