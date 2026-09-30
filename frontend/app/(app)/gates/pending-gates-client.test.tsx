import { renderWithIntl as render } from "@/test/render-with-intl";
import type { ReactNode } from "react";

import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PendingHumanGate } from "@/graphql/workflows/tiered.types";

import { PendingGatesClient } from "./pending-gates-client";

const state = vi.hoisted(() => ({
  gates: [] as PendingHumanGate[],
  loading: false,
  error: undefined as { message: string } | undefined,
  refetch: vi.fn(),
}));

vi.mock("@/graphql/workflows/tiered.hooks", () => ({
  usePendingHumanGates: () => ({
    gates: state.gates,
    loading: state.loading,
    error: state.error,
    refetch: state.refetch,
  }),
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/i18n/formatters", () => ({
  useFormatters: () => ({
    formatRelativeTime: () => "5 minutes ago",
  }),
}));

// PageShell reads the app-chrome context; render a thin shell so the test
// targets this client rather than page chrome.
vi.mock("@/components/PageShell", () => ({
  PageShell: ({ actions, children }: { actions?: ReactNode; children?: ReactNode }) => (
    <div>
      <div>{actions}</div>
      {children}
    </div>
  ),
}));

function gate(overrides: Partial<PendingHumanGate>): PendingHumanGate {
  return {
    executionId: "72",
    runGuid: "run-guid-1",
    workflowId: "WorkflowDefinitionRunWorkflow-320",
    definitionSlug: "outreach-review",
    definitionName: "Outreach Review",
    stageRole: "outreach review",
    stageApprovers: ["team:gtm"],
    startedAt: "2026-09-20T10:00:00Z",
    ...overrides,
  };
}

describe("PendingGatesClient", () => {
  it("shows a loading skeleton before any data has arrived", () => {
    state.gates = [];
    state.loading = true;
    state.error = undefined;
    const { container } = render(<PendingGatesClient />);
    expect(container.querySelector('[class*="animate-pulse"]')).toBeTruthy();
  });

  it("shows an empty state with no pending gates", () => {
    state.gates = [];
    state.loading = false;
    state.error = undefined;
    render(<PendingGatesClient />);
    expect(screen.getByText("No pending gates")).toBeVisible();
  });

  it("surfaces the query error", () => {
    state.gates = [];
    state.loading = false;
    state.error = { message: "boom" };
    render(<PendingGatesClient />);
    expect(screen.getByText("boom")).toBeVisible();
    state.error = undefined;
  });

  it("lists each gate with a deep link to the run's observe page", () => {
    state.gates = [gate({})];
    state.loading = false;
    state.error = undefined;
    render(<PendingGatesClient />);

    const link = screen.getByRole("link", { name: "Outreach Review" });
    expect(link).toHaveAttribute("href", "/workflows/outreach-review/observe?run=run-guid-1");
    expect(screen.getByText("outreach review")).toBeVisible();
    expect(screen.getByText(/team:gtm/)).toBeVisible();
    expect(screen.getByText(/5 minutes ago/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Review" })).toHaveAttribute(
      "href",
      "/workflows/outreach-review/observe?run=run-guid-1"
    );
  });

  it("falls back to the slug when a gate carries no definition name", () => {
    state.gates = [gate({ definitionName: "" })];
    state.loading = false;
    render(<PendingGatesClient />);
    expect(screen.getByRole("link", { name: "outreach-review" })).toBeVisible();
  });

  it("refetches when the refresh action is clicked", () => {
    state.gates = [gate({})];
    state.loading = false;
    state.refetch.mockClear();
    render(<PendingGatesClient />);
    fireEvent.click(screen.getByRole("button", { name: "Refresh pending gates" }));
    expect(state.refetch).toHaveBeenCalledTimes(1);
  });
});
