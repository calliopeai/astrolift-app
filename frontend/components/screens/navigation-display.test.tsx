import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";

import messages from "@/messages/en.json";
import { useLocalListState } from "@/components/list/use-list-state";
import { TooltipProvider } from "@/components/ui/tooltip";
import { MetricsScreen } from "./metrics/MetricsScreen";
import { METRICS_APPS_LIST } from "./metrics/metrics-apps-list";
import { METRICS } from "./ops/metrics-ops-shell.fixtures";
import { ProjectDetailScreen } from "./projects/ProjectDetailScreen";
import { DETAIL } from "./projects/projects-detail.fixtures";
import { WorkflowBuilderScreen } from "./workflows/new/WorkflowBuilderScreen";
import { BUILDER } from "./workflows/new/workflows-new-builder.fixtures";

vi.mock("@/components/PageShell", () => ({
  PageShell: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => false, loading: false }),
}));

const Context = ({ children }: { children: ReactNode }) => (
  <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
    <TooltipProvider>{children}</TooltipProvider>
  </NextIntlClientProvider>
);

it("uses an actual agent destination when an agent-only project has no workflow anchor", () => {
  render(<ProjectDetailScreen {...DETAIL} apps={[]} workflows={[]} workflowRuns={[]} />, {
    wrapper: Context,
  });
  expect(screen.getByText("Needs attention").closest("a")).toHaveAttribute(
    "href",
    `/agents?project=${encodeURIComponent(DETAIL.project!.slug)}`
  );
  expect(document.getElementById("workflows")).toBeNull();
});

it("shows no Temporal shortcut until an operator URL is configured", () => {
  function Metrics({ url }: { url?: string }) {
    const list = useLocalListState(METRICS_APPS_LIST);
    return <MetricsScreen {...METRICS} list={list} temporalUiUrl={url} />;
  }
  const view = render(<Metrics />, { wrapper: Context });
  expect(screen.queryByRole("link", { name: messages.metrics.temporalUi })).not.toBeInTheDocument();
  view.rerender(<Metrics url="https://workflows.operator.example/" />);
  expect(screen.getByRole("link", { name: messages.metrics.temporalUi })).toHaveAttribute(
    "href",
    "https://workflows.operator.example/"
  );
  expect(document.querySelector('[href="http://localhost:8233"]')).toBeNull();
});

it("waits for workflow entitlement without reporting denied access or accepting submit", () => {
  const onCreate = vi.fn().mockResolvedValue({});
  const view = render(
    <WorkflowBuilderScreen
      {...BUILDER}
      initialStep={3}
      canCreate={false}
      entitlementLoading
      onCreate={onCreate}
    />,
    { wrapper: Context }
  );
  const button = screen.getByRole("button", { name: "Create workflow" });
  expect(button).toBeDisabled();
  expect(button).not.toHaveAttribute("title");
  fireEvent.submit(button.closest("form")!);
  expect(onCreate).not.toHaveBeenCalled();
  view.rerender(
    <WorkflowBuilderScreen
      {...BUILDER}
      initialStep={3}
      canCreate={false}
      entitlementLoading={false}
      onCreate={onCreate}
    />
  );
  expect(button).toHaveAttribute("title", "You don't have create access");
  view.rerender(<WorkflowBuilderScreen {...BUILDER} initialStep={3} onCreate={onCreate} />);
  expect(button).toBeEnabled();
});
