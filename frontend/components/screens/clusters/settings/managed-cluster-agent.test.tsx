import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import { ManagedClusterAgent } from "./ManagedClusterAgent";

const connected = {
  id: "install-one",
  clusterId: "cluster-one",
  requestId: "request-one",
  status: "SUCCEEDED",
  secretConfirmed: true,
  deploymentConfirmed: true,
  heartbeatConfirmed: true,
  retryable: false,
  errorCode: "",
  errorMessage: "",
};
function harness(mode: "connected" | "uncertain" | "pending" = "connected") {
  const requests: { operationName: string; variables: Record<string, unknown> }[] = [];
  let current =
    mode === "connected"
      ? connected
      : {
          ...connected,
          status: mode === "pending" ? "AWAITING_HEARTBEAT" : "UNCERTAIN",
          heartbeatConfirmed: false,
          retryable: mode === "uncertain",
          errorMessage: mode === "uncertain" ? "Retry the same installation" : "",
        };
  let starts = 0;
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://test.invalid/graphql/",
      fetch: vi.fn(async (_url, init) => {
        const request = JSON.parse(String(init?.body));
        requests.push(request);
        let data;
        if (request.operationName === "ReviewClusterAgentInstall") {
          data = {
            astroliftClusterAgentInstallReview: {
              clusterId: request.variables.clusterId,
              version: 7,
              source: "review-proof",
            },
          };
        } else if (request.operationName === "InstallClusterAgent") {
          starts++;
          if (starts > 1) current = connected;
          data = { installClusterAgent: { ok: true, errors: [], data: current } };
        } else {
          data = { astroliftClusterAgentInstall: current };
        }
        return new Response(JSON.stringify({ data }), {
          headers: { "Content-Type": "application/json" },
        });
      }),
    }),
  });
  return {
    requests,
    mount: () =>
      render(
        <ApolloProvider client={client}>
          <NextIntlClientProvider locale="en" messages={messages}>
            <ManagedClusterAgent clusterId="cluster-one" />
          </NextIntlClientProvider>
        </ApolloProvider>
      ),
  };
}

it("installs through a bound managed review without exposing or issuing a manual key", async () => {
  const h = harness();
  h.mount();
  await userEvent.click(
    screen.getByRole("button", { name: messages.clusterSettings.agent.deploy })
  );
  expect(await screen.findByText(messages.clusterSettings.agent.managedConnected)).toBeTruthy();
  const writes = h.requests.filter((r) => r.operationName === "InstallClusterAgent");
  expect(writes).toHaveLength(1);
  expect(writes[0].variables.input).toEqual({
    clusterId: "cluster-one",
    expectedVersion: 7,
    expectedSource: "review-proof",
    requestId: expect.any(String),
  });
  expect(
    h.requests.some((r) => ["IssueClusterAgentKey", "DeployClusterAgent"].includes(r.operationName))
  ).toBe(false);
  expect(screen.queryByText(messages.clusterSettings.agent.rotate)).toBeNull();
});

it("resumes uncertain work with the same request and source rather than creating another credential", async () => {
  const h = harness("uncertain");
  h.mount();
  await userEvent.click(screen.getByRole("button"));
  expect(await screen.findByRole("alert")).toHaveTextContent("Retry the same installation");
  await userEvent.click(screen.getByRole("button"));
  await waitFor(() =>
    expect(h.requests.filter((r) => r.operationName === "InstallClusterAgent")).toHaveLength(2)
  );
  const writes = h.requests.filter((r) => r.operationName === "InstallClusterAgent");
  expect(writes[0].variables).toEqual(writes[1].variables);
  expect(h.requests.filter((r) => r.operationName === "ReviewClusterAgentInstall")).toHaveLength(1);
});

it("does not report success before the replacement agent's heartbeat is confirmed", async () => {
  const h = harness("pending");
  h.mount();
  await userEvent.click(screen.getByRole("button"));
  expect(await screen.findByText(messages.clusterSettings.agent.managedPending)).toBeTruthy();
  expect(screen.queryByText(messages.clusterSettings.agent.managedConnected)).toBeNull();
  expect(screen.getByRole("button")).toBeDisabled();
});
