import { screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { useLocalListState } from "@/components/list/use-list-state";
import { WORKFLOW_INSTANCES_LIST } from "@/components/screens/workflows/list/workflow-instances-list";
import { INSTANCES } from "@/components/screens/workflows/list/workflows-list.fixtures";
import { renderWithIntl } from "@/test/render-with-intl";

import PlatformActivityPage from "./page";

const state = vi.hoisted(() => ({ canView: true, loading: false, reads: vi.fn() }));

vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ loading: state.loading, can: () => state.canView }),
}));
vi.mock("@/graphql/workflows/tiered.hooks", () => ({
  useWorkflowsEntitlement: () => ({ canRun: false }),
}));
vi.mock("@/components/screens/workflows/list/use-workflow-instances", () => ({
  useWorkflowInstancesList: () => {
    state.reads();
    return {
      list: useLocalListState(WORKFLOW_INSTANCES_LIST),
      rows: INSTANCES,
      totalCount: INSTANCES.length,
      loading: false,
      stale: false,
      error: null,
      onRetry: () => {},
      instanceHref: (i: { workflowId: string }) => `?instance=${encodeURIComponent(i.workflowId)}`,
      selectedWorkflowId: null,
      onCloseInstance: () => {},
    };
  },
}));

beforeEach(() => {
  state.canView = true;
  state.loading = false;
  state.reads.mockClear();
});

it("renders actual instance rows and links to their history on the activity route", () => {
  renderWithIntl(<PlatformActivityPage />);
  expect(state.reads).toHaveBeenCalledOnce();
  expect(screen.getByRole("heading", { name: "Platform activity" })).toBeInTheDocument();
  expect(screen.getByText(INSTANCES[0].workflowId).closest("a")).toHaveAttribute(
    "href",
    `?instance=${encodeURIComponent(INSTANCES[0].workflowId)}`
  );
  expect(screen.queryByText("Running instances")).not.toBeInTheDocument();
});

it.each([false, true])(
  "does not mount instance reads while access is unavailable (loading=%s)",
  (loading) => {
    state.canView = false;
    state.loading = loading;
    renderWithIntl(<PlatformActivityPage />);
    expect(state.reads).not.toHaveBeenCalled();
    expect(screen.queryByText(INSTANCES[0].workflowId)).not.toBeInTheDocument();
  }
);
