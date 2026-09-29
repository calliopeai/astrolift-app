import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ModelsClient } from "./models-client";

const models = [
  {
    id: "m1",
    name: "qwen",
    variant: "vllm",
    status: "active",
    statusError: "",
    config: { model: "Qwen/Qwen3-8B", gpu: 2 },
    registeredAppSlug: "chat",
    projectSlug: "",
    ownerScope: "app",
    clusterSlug: "c1",
    environmentName: "prod",
    deployedByEmail: "leo@example.com",
    deployedByMe: true,
  },
  {
    id: "m2",
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

const page = (items: typeof models) => ({
  astroliftModelEndpointsPage: { items, totalCount: items.length, page: 1, pageSize: 25 },
});

let result: { data?: unknown; loading: boolean; error?: Error } = {
  data: page(models),
  loading: false,
};
let lastOptions: { variables?: unknown } | undefined;

vi.mock("@apollo/client/react", () => ({
  useQuery: (_query: unknown, options: { variables?: unknown }) => {
    lastOptions = options;
    return result;
  },
  useMutation: () => [vi.fn(), { loading: false }],
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
// The list keeps its view, filters and page in the URL.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/models",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

describe("ModelsClient", () => {
  it("lists hosted and cloud models with their hardware and owner", () => {
    render(<ModelsClient />);
    expect(screen.getByText("Qwen/Qwen3-8B")).toBeVisible();
    expect(screen.getByText("2 GPUs")).toBeVisible();
    expect(screen.getByText("cloud")).toBeVisible();
    expect(screen.getByRole("link", { name: "chat · prod" })).toHaveAttribute(
      "href",
      "/apps/chat/managed-services"
    );
    expect(screen.getByRole("link", { name: "shared" })).toHaveAttribute(
      "href",
      "/projects/shared/resources"
    );
  });

  it("asks the server for the first page by name, with no filter", () => {
    render(<ModelsClient />);
    expect(lastOptions?.variables).toEqual({
      search: null,
      filter: null,
      sort: "name",
      page: 1,
      pageSize: 25,
    });
  });

  it("says so when there are no models", () => {
    result = { data: page([]), loading: false };
    render(<ModelsClient />);
    expect(screen.getByText("No models yet")).toBeVisible();
  });
});
