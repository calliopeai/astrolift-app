import { ApolloClient, ApolloLink, InMemoryCache, Observable } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useFailing } from "./use-failing";
import { recentDeploymentsCount, useRecentDeployments } from "./use-recent-deployments";
import { useWaiting } from "./use-waiting";

// The viewer may see every source, so each panel reads everything it can.
vi.mock("@/graphql/user/user.hooks", () => ({ useModules: () => ({ canView: () => true }) }));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true }),
}));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: "org-1" } }),
}));

/** Every operation the panels send, by name; the responses never arrive. */
function countingClient(sent: string[]) {
  return new ApolloClient({
    cache: new InMemoryCache(),
    link: new ApolloLink((operation) => {
      sent.push(operation.operationName ?? "anonymous");
      return new Observable(() => {});
    }),
  });
}

/** The three Home panels that show deployments, mounted together as the Apps layout mounts them. */
function DeploymentPanels() {
  useWaiting();
  useFailing();
  useRecentDeployments();
  return null;
}

const DEPLOYMENT_OPERATIONS = new Set(["ListDeployments", "ListDeploymentsPage"]);

describe("Home's deployment panels", () => {
  it("fetch deployments once between Waiting on you, Failing and Recent deployments", () => {
    const sent: string[] = [];
    render(
      <ApolloProvider client={countingClient(sent)}>
        <DeploymentPanels />
      </ApolloProvider>
    );
    const deployments = sent.filter((name) => DEPLOYMENT_OPERATIONS.has(name));
    // Before this read was shared: ListDeployments { limit: 100 } for Waiting
    // on you and Failing, and ListDeploymentsPage { limit: 5 } for Recent
    // deployments, 2 deployment operations of 5.
    expect(deployments).toEqual(["ListDeployments"]);
    expect([...sent].sort()).toEqual([
      "ListAgentTasksPage",
      "ListDeployments",
      "ListPendingHumanGates",
      "ListSecretChangeProposals",
    ]);
  });

  it("count every deploy while the shared read holds them all, and none once it is full", () => {
    expect(recentDeploymentsCount({ deployments: [1, 2, 3], capped: false })).toBe(3);
    expect(recentDeploymentsCount({ deployments: [], capped: false })).toBe(0);
    expect(recentDeploymentsCount({ deployments: Array(100).fill(0), capped: true })).toBeNull();
  });
});
