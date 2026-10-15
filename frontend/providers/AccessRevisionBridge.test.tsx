import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AccessRevisionBridge } from "./AccessRevisionBridge";

vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: "org-1" }, loading: false }),
}));

/** A client whose server answers each revision read with the next value. */
function clientAnswering(revisions: string[]) {
  const answers = [...revisions];
  const reads = vi.fn();
  const link = new ApolloLink(
    () =>
      new Observable((observer) => {
        reads();
        const revision = answers.length > 1 ? answers.shift() : answers[0];
        observer.next({ data: { astroliftAccessRevision: revision } });
        observer.complete();
      })
  );
  const client = new ApolloClient({ cache: new InMemoryCache(), link });
  const reset = vi.spyOn(client, "resetStore").mockResolvedValue(null);
  return { client, reads, reset };
}

function mount(client: ApolloClient) {
  render(
    <ApolloProvider client={client}>
      <AccessRevisionBridge />
    </ApolloProvider>
  );
}

describe("AccessRevisionBridge", () => {
  it("resets the cache when the revision moves after the first read", async () => {
    const { client, reads, reset } = clientAnswering(["r1", "r2"]);
    mount(client);
    await waitFor(() => expect(reads).toHaveBeenCalledTimes(1));
    expect(reset).not.toHaveBeenCalled();
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => expect(reset).toHaveBeenCalledTimes(1));
  });

  it("leaves the cache alone while the revision holds", async () => {
    const { client, reads, reset } = clientAnswering(["r1"]);
    mount(client);
    await waitFor(() => expect(reads).toHaveBeenCalledTimes(1));
    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => expect(reads).toHaveBeenCalledTimes(2));
    expect(reset).not.toHaveBeenCalled();
  });
});
