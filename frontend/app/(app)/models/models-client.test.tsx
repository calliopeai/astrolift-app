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
  },
];

let result: { data?: unknown; loading: boolean; error?: Error } = {
  data: { astroliftModelEndpoints: models },
  loading: false,
};

vi.mock("@apollo/client/react", () => ({
  useQuery: () => result,
  useMutation: () => [vi.fn(), { loading: false }],
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
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

  it("says so when there are no models", () => {
    result = { data: { astroliftModelEndpoints: [] }, loading: false };
    render(<ModelsClient />);
    expect(screen.getByText("No models yet")).toBeVisible();
  });
});
