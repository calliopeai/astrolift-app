import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { renderWithIntl as render } from "@/test/render-with-intl";
import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { buildSchema, validate } from "graphql";
import { readFileSync } from "node:fs";
import messages from "@/messages/en.json";
import { LIST_MODEL_ENDPOINTS_PAGE } from "@/graphql/models/models.queries";
import type { ModelEndpoint } from "@/components/screens/models/models-list";
import ModelEndpointsPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/models/endpoints",
  useSearchParams: () => new URLSearchParams(),
}));
const models: ModelEndpoint[] = [
  {
    id: "00000000-0000-4000-8000-000000000001",
    name: "qwen",
    variant: "vllm",
    status: "active",
    statusError: "",
    config: { model: "Qwen/Qwen3-8B", compute_mode: "gpu", gpu: 2 },
    registeredAppSlug: "chat",
    projectSlug: "",
    ownerScope: "app",
    clusterSlug: "c1",
    environmentName: "prod",
    deployedByEmail: "leo@example.com",
    deployedByMe: true,
  },
  {
    id: "00000000-0000-4000-8000-000000000002",
    name: "claude",
    variant: "bedrock",
    status: "active",
    statusError: "",
    config: { model_id: "anthropic.claude" },
    registeredAppSlug: "",
    projectSlug: "shared",
    ownerScope: "project",
    clusterSlug: "c1",
    environmentName: "",
    deployedByEmail: "",
    deployedByMe: false,
  },
];
type Request = { operationName: string; variables: Record<string, unknown> };
let requests: Request[], rows: ModelEndpoint[];
function view() {
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "http://control-plane.test/app/gql/config/",
      fetch: async (_uri, options) => {
        const request: Request = JSON.parse(String(options?.body));
        requests.push(request);
        if (request.operationName !== "ListModelEndpointsPage")
          throw new Error(`Unexpected ${request.operationName}`);
        return new Response(
          JSON.stringify({
            data: {
              astroliftModelEndpointsPage: {
                items: rows,
                totalCount: rows.length,
                page: request.variables.page,
                pageSize: request.variables.pageSize,
              },
            },
          }),
          { headers: { "Content-Type": "application/json" } }
        );
      },
    }),
  });
  return render(
    <ApolloProvider client={client}>
      <ModelEndpointsPage />
    </ApolloProvider>
  );
}
const text = messages.models.shared.deployments;
beforeEach(() => {
  requests = [];
  rows = models;
});

describe("legacy /models/endpoints route", () => {
  it("uses the actual SDL endpoint document", () => {
    expect(
      validate(
        buildSchema(readFileSync(`${process.cwd()}/schema.graphql`, "utf8")),
        LIST_MODEL_ENDPOINTS_PAGE
      )
    ).toEqual([]);
  });
  it("renders persisted compute mode, provider-managed endpoints and exact legacy owners", async () => {
    view();
    expect(await screen.findByText("Qwen/Qwen3-8B")).toBeVisible();
    expect(within(screen.getByRole("row", { name: /qwen/ })).getByText(text.gpu)).toBeVisible();
    expect(
      within(screen.getByRole("row", { name: /claude/ })).getByText(text.notApplicable)
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "chat · prod" })).toHaveAttribute(
      "href",
      "/apps/chat/managed-services"
    );
    expect(screen.getByRole("link", { name: "shared" })).toHaveAttribute(
      "href",
      "/projects/shared/resources"
    );
    expect(screen.queryByText("2 GPUs")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: messages.playground.title })).toHaveAttribute(
      "href",
      "/playground"
    );
    expect(
      screen
        .getAllByRole("link", { name: text.title })
        .every((link) => link.getAttribute("href") === "/models")
    ).toBe(true);
  });
  it.each([0, 2])(
    "does not infer hosted CPU/GPU mode from gpu=%s without a persisted compute fact",
    async (gpu) => {
      rows = [{ ...models[0], config: { model: "Qwen/Qwen3-8B", gpu } }];
      view();
      await screen.findByText("Qwen/Qwen3-8B");
      const row = within(screen.getByRole("row", { name: /qwen/ }));
      expect(row.getByText(text.unknown)).toBeVisible();
      expect(row.queryByText(text.cpu)).not.toBeInTheDocument();
      expect(row.queryByText(text.gpu)).not.toBeInTheDocument();
      expect(row.queryByText(text.notApplicable)).not.toBeInTheDocument();
    }
  );
  it("uses an explicit persisted CPU fact on a hosted endpoint", async () => {
    rows = [{ ...models[0], config: { model: "Qwen/Qwen3-8B", compute_mode: "cpu", gpu: 0 } }];
    view();
    await screen.findByText("Qwen/Qwen3-8B");
    expect(within(screen.getByRole("row", { name: /qwen/ })).getByText(text.cpu)).toBeVisible();
  });
  it("asks the legacy server endpoint for the first numbered page by name with no filter", async () => {
    view();
    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0].operationName).toBe("ListModelEndpointsPage");
    expect(requests[0].variables).toEqual({
      search: null,
      filter: null,
      sort: "name",
      page: 1,
      pageSize: 25,
    });
  });
  it("retains its legacy empty state and separate deployment route", async () => {
    rows = [];
    view();
    expect(await screen.findByText(text.legacyEmptyTitle)).toBeVisible();
    expect(
      screen
        .getAllByRole("link", { name: text.legacyDeploy })
        .every((link) => link.getAttribute("href") === "/models/deploy/legacy")
    ).toBe(true);
  });
});
