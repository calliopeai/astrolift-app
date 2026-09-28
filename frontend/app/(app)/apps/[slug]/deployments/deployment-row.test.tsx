import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { DeploymentRow } from "./deployments-client";

// Why a deploy failed, or what a pending one waits on, on the row itself (#2123).

vi.mock("@apollo/client/react", () => ({
  useQuery: () => ({ data: undefined, loading: false }),
  useMutation: () => [vi.fn(), { loading: false }],
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true, granted: new Set(), loading: false }),
}));

function row(overrides: Partial<AstroliftDeployment>) {
  const d = {
    id: "d1",
    status: "pending",
    imageTag: "main-abc1234",
    environmentName: "prod",
    registeredAppSlug: "veruus-demo",
    createdAt: new Date().toISOString(),
    statusReason: "",
    ...overrides,
  } as AstroliftDeployment;
  render(
    <table>
      <tbody>
        <DeploymentRow
          deployment={d}
          isOpen={false}
          selected={false}
          onToggleSelect={() => {}}
          onToggleOpen={() => {}}
          app={{ slug: "veruus-demo" } as AstroliftRegisteredApp}
        />
      </tbody>
    </table>
  );
}

describe("DeploymentRow (#2123)", () => {
  it("says what a pending deploy is waiting on", () => {
    row({ statusReason: "Queued behind deploy main-9f8e7d6, still deploying." });

    expect(
      screen.getByText("Queued behind deploy main-9f8e7d6, still deploying.")
    ).toBeInTheDocument();
  });

  it("says why a deploy failed", () => {
    row({ status: "failed", statusReason: "superseded by a newer deploy" });

    expect(screen.getByText("superseded by a newer deploy")).toBeInTheDocument();
  });

  it("adds nothing for a running deploy", () => {
    row({ status: "running", statusReason: "" });

    expect(screen.queryByTitle(/./)).not.toBeInTheDocument();
  });
});
