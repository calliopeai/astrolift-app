import { MockedProvider } from "@apollo/client/testing/react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import { TooltipProvider } from "@/components/ui/tooltip";
import { RESTART_WORKLOAD } from "@/graphql/lifecycle/lifecycle.mutations";
import { WorkloadOpsRowView } from "./ControlsSection";
import { WORKLOAD_WEB } from "./app-controls.fixtures";
import { useWorkloadOps } from "./use-controls-section";
const feedback = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock("sonner", () => ({ toast: feedback }));
function LiveRow() {
  return <WorkloadOpsRowView {...useWorkloadOps(WORKLOAD_WEB)} />;
}
it("keeps the actual workload restart prompt through a rejected mutation and retries only after confirmation", async () => {
  const rejected = vi.fn(() => ({
    data: {
      restartAstroliftWorkload: {
        ok: false,
        errors: [{ code: "UPSTREAM_FAILURE", message: "actual restart diagnostic", field: null }],
        data: null,
      },
    },
  }));
  const succeeded = vi.fn(() => ({
    data: {
      restartAstroliftWorkload: {
        ok: true,
        errors: [],
        data: {
          workloadId: WORKLOAD_WEB.id,
          newRevision: 2,
          desiredReplicas: 3,
          readyReplicas: null,
        },
      },
    },
  }));
  const request = {
    query: RESTART_WORKLOAD,
    variables: { input: { workloadId: WORKLOAD_WEB.id }, ifMatchVersion: WORKLOAD_WEB.version },
  };
  render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <MockedProvider
        mocks={[
          { request, result: rejected, delay: 25 },
          { request, result: succeeded, delay: 25 },
        ]}
      >
        <TooltipProvider>
          <LiveRow />
        </TooltipProvider>
      </MockedProvider>
    </NextIntlClientProvider>
  );
  fireEvent.click(screen.getByRole("button", { name: "Rolling restart" }));
  expect(screen.getByRole("alertdialog")).toHaveTextContent(WORKLOAD_WEB.name);
  expect(rejected).not.toHaveBeenCalled();
  expect(succeeded).not.toHaveBeenCalled();
  fireEvent.click(
    within(screen.getByRole("alertdialog")).getByRole("button", { name: "Rolling restart" })
  );
  expect(
    within(screen.getByRole("alertdialog")).getByRole("button", { name: "Working…" })
  ).toBeDisabled();
  await waitFor(() =>
    expect(feedback.error).toHaveBeenCalledExactlyOnceWith("actual restart diagnostic")
  );
  const retained = screen.getByRole("alertdialog");
  expect(retained).toHaveTextContent(WORKLOAD_WEB.name);
  expect(feedback.success).not.toHaveBeenCalled();
  expect(succeeded).not.toHaveBeenCalled();
  fireEvent.click(within(retained).getByRole("button", { name: "Rolling restart" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  expect(rejected).toHaveBeenCalledTimes(1);
  expect(succeeded).toHaveBeenCalledTimes(1);
  expect(feedback.success).toHaveBeenCalledTimes(1);
  expect(feedback.error).toHaveBeenCalledTimes(1);
});
