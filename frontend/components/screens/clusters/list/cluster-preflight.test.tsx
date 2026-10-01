import { ApolloClient, ApolloLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, renderHook } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { Observable } from "rxjs";
import { expect, it } from "vitest";

import messages from "@/messages/en.json";

import { CLUSTERS } from "./fixtures";
import { useClusterActions } from "./use-cluster-actions";

it("sends full preflight explicitly and leaves ordinary refresh lightweight", async () => {
  const inputs: unknown[] = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink(
      (operation) =>
        new Observable((observer) => {
          if (operation.operationName === "RefreshClusterManagement") {
            inputs.push(operation.variables.input);
            observer.next({
              data: {
                refreshClusterManagement: {
                  ok: true,
                  errors: [],
                  data: {
                    id: CLUSTERS[0].id,
                    slug: CLUSTERS[0].slug,
                    lifecycle: "managed",
                    lastManagementError: "",
                    managedAt: null,
                  },
                },
              },
            });
          } else {
            observer.next({ data: { astroliftTenantClusters: [] } });
          }
          observer.complete();
        })
    ),
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
      <ApolloProvider client={client}>{children}</ApolloProvider>
    </NextIntlClientProvider>
  );
  const { result } = renderHook(useClusterActions, { wrapper });
  await act(() => result.current.onRefresh(CLUSTERS[0], true));
  await act(() => result.current.onRefresh(CLUSTERS[0]));
  expect(inputs).toEqual([
    { clusterId: CLUSTERS[0].id, forcePreflight: true },
    { clusterId: CLUSTERS[0].id, forcePreflight: false },
  ]);
  client.stop();
});
