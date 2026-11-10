import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import { NvkeWorkflowPanel, type NvkeWorkflowPanelProps } from "./NvkeWorkflowPanel";
import { WORKFLOW_PROPS, WORKFLOW_HISTORY } from "./NvkeWorkflowPanel.stories";
const copy = messages.nvke.workflow;
const wrapper = ({ children }: { children: ReactNode }) => (
  <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
    {children}
  </NextIntlClientProvider>
);
const props = (): NvkeWorkflowPanelProps => ({
  ...WORKFLOW_PROPS,
  onRequest: vi.fn(async () => true),
  onRecover: vi.fn(async () => true),
  onHistory: vi.fn(),
  onRefresh: vi.fn(),
});
async function chooseWorkflow(name: string) {
  fireEvent.click(screen.getByRole("combobox"));
  fireEvent.click(await screen.findByRole("option", { name }));
}
const confirm = () =>
  fireEvent.click(
    within(screen.getByRole("alertdialog")).getByRole("button", {
      name: messages.shared.confirmation.confirm,
    })
  );
function openReset() {
  fireEvent.click(
    screen
      .getAllByRole("button", { name: copy.selectBoundary })
      .find((button) => !(button as HTMLButtonElement).disabled)!
  );
}
it("preserves native JSON text and keeps it out of confirmation", async () => {
  const p = props();
  render(<NvkeWorkflowPanel {...p} />, { wrapper });
  fireEvent.click(screen.getByRole("button", { name: copy.submit }));
  const raw =
    '{ "clusterID":"' +
    p.clusterId +
    '", "thresholdDays":9007199254740993, "force":true, "PRIVATE_FIELD":"PRIVATE_INPUT" }';
  fireEvent.change(screen.getByLabelText(messages.nvke.parameters), { target: { value: raw } });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  const review = await screen.findByRole("alertdialog");
  expect(review).not.toHaveTextContent("PRIVATE_INPUT");
  expect(review).toHaveTextContent(messages.nvke.maintenance.forced);
  expect(review).toHaveTextContent(messages.nvke.maintenance.yes);
  expect(review).not.toHaveTextContent("9007199254740992");
  expect(review).toHaveTextContent("site version " + p.site.version);
  expect(review).toHaveTextContent("native authority version " + p.authority!.version);
  expect(review).toHaveTextContent(p.clusterId);
  expect(p.onRequest).not.toHaveBeenCalled();
  confirm();
  await waitFor(() => expect(p.onRequest).toHaveBeenCalledOnce());
  expect(p.onRequest).toHaveBeenCalledWith(
    expect.objectContaining({ mode: "workflow", workflow: "RenewK8sCertsV2", parametersText: raw })
  );
  expect(screen.queryByDisplayValue(raw)).not.toBeInTheDocument();
});
it("requires retirement acknowledgement and reviews the exact node and retained owner", async () => {
  const p = props();
  render(<NvkeWorkflowPanel {...p} />, { wrapper });
  fireEvent.click(screen.getByRole("button", { name: copy.submit }));
  await chooseWorkflow("RetireLostEtcdRotationNode");
  const node = "123e4567-e89b-12d3-a456-426614174099";
  fireEvent.change(screen.getByLabelText(messages.nvke.parameters), {
    target: {
      value: JSON.stringify({
        clusterID: p.clusterId,
        nodeID: node,
        rotationWorkflowID: p.owners.RotateEtcdEncryptionKey,
      }),
    },
  });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  await screen.findByRole("alert");
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  expect(p.onRequest).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("checkbox", { name: copy.retireAck }));
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  const review = await screen.findByRole("alertdialog");
  expect(review).toHaveTextContent(node);
  expect(review).toHaveTextContent(p.owners.RotateEtcdEncryptionKey!);
  confirm();
  await waitFor(() => expect(p.onRequest).toHaveBeenCalledOnce());
  expect(p.onRequest).toHaveBeenCalledWith(
    expect.objectContaining({
      workflow: "RetireLostEtcdRotationNode",
      acknowledgeLostNodeTeardown: true,
    })
  );
});
it("requires an explicit reset mode and both acknowledgements, preserves event strings and clears private evidence", async () => {
  const p = props();
  render(<NvkeWorkflowPanel {...p} />, { wrapper });
  openReset();
  const form = within(screen.getByLabelText(copy.rationale).closest("form")!);
  fireEvent.change(screen.getByLabelText(copy.rationale), { target: { value: "PRIVATE_REASON" } });
  fireEvent.change(screen.getByLabelText(copy.evidence), { target: { value: "PRIVATE_EVIDENCE" } });
  fireEvent.click(form.getByRole("button", { name: messages.nvke.reviewRequest }));
  await screen.findByRole("alert");
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  fireEvent.click(form.getByRole("combobox"));
  fireEvent.click(await screen.findByRole("option", { name: "restart_rewrite" }));
  fireEvent.click(form.getByRole("checkbox", { name: copy.boundaryAck }));
  fireEvent.click(form.getByRole("checkbox", { name: copy.nodeAck }));
  fireEvent.click(form.getByRole("button", { name: messages.nvke.reviewRequest }));
  const review = await screen.findByRole("alertdialog");
  expect(review).toHaveTextContent("9007199254740993");
  expect(review).toHaveTextContent("9007199254740992");
  expect(review).toHaveTextContent(WORKFLOW_HISTORY.runId);
  expect(review).not.toHaveTextContent("PRIVATE");
  fireEvent.click(
    within(review).getByRole("button", { name: messages.shared.confirmation.confirm })
  );
  await waitFor(() => expect(p.onRequest).toHaveBeenCalledOnce());
  expect(p.onRequest).toHaveBeenCalledWith({
    mode: "reset",
    timeoutSeconds: 7200,
    review: {
      workflowId: WORKFLOW_HISTORY.workflowId,
      baseRunId: WORKFLOW_HISTORY.runId,
      phase: "rewrite",
      mode: "restart_rewrite",
      workflowTaskEventId: "9007199254740992",
      activityEventId: "9007199254740993",
      activityType: "StartEtcdEncryptionRewrite",
      rationale: "PRIVATE_REASON",
      quiescenceEvidence: "PRIVATE_EVIDENCE",
      acknowledgeBoundaryReviewed: true,
      acknowledgeConflictingWorkStopped: true,
      retirementWorkflowId: "",
    },
  });
  expect(screen.queryByDisplayValue("PRIVATE_REASON")).not.toBeInTheDocument();
  expect(screen.queryByDisplayValue("PRIVATE_EVIDENCE")).not.toBeInTheDocument();
});
it.each(["COMPLETED", "RUNNING"])("refuses reset controls for a %s owner", (status) => {
  const p = props();
  render(<NvkeWorkflowPanel {...p} history={{ ...WORKFLOW_HISTORY, status }} />, { wrapper });
  expect(
    screen
      .getAllByRole("button", { name: copy.selectBoundary })
      .every((button) => (button as HTMLButtonElement).disabled)
  ).toBe(true);
});
it("does not offer the Begin activity as a reset shortcut", () => {
  render(<NvkeWorkflowPanel {...props()} />, { wrapper });
  expect(screen.getAllByRole("button", { name: copy.selectBoundary })[0]).toBeDisabled();
});
it.each(["scope", "authority", "source", "run", "permission"])(
  "discards private reset draft when %s changes",
  (change) => {
    const p = props();
    const view = render(<NvkeWorkflowPanel {...p} />, { wrapper });
    openReset();
    fireEvent.change(screen.getByLabelText(copy.rationale), {
      target: { value: "PRIVATE_REASON" },
    });
    const next = { ...p };
    if (change === "scope") next.scopeKey = "another-actor";
    if (change === "authority")
      next.authority = { ...p.authority!, version: p.authority!.version + 1 };
    if (change === "source") next.source = { ...p.source, version: p.source.version + 1 };
    if (change === "run")
      next.history = { ...p.history!, runId: "123e4567-e89b-12d3-a456-426614174098" };
    if (change === "permission") next.canManage = false;
    view.rerender(<NvkeWorkflowPanel {...next} />);
    expect(screen.queryByDisplayValue("PRIVATE_REASON")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(p.onRequest).not.toHaveBeenCalled();
  }
);
it("refuses stale and mismatched history", () => {
  const p = props();
  const first = render(
    <NvkeWorkflowPanel {...p} historyTable={{ ...p.historyTable, isStale: true }} />,
    { wrapper }
  );
  expect(
    screen
      .getAllByRole("button", { name: copy.selectBoundary })
      .every((button) => (button as HTMLButtonElement).disabled)
  ).toBe(true);
  first.unmount();
  render(
    <NvkeWorkflowPanel
      {...p}
      history={{ ...p.history!, clusterId: "123e4567-e89b-12d3-a456-426614174097" }}
    />,
    { wrapper }
  );
  expect(screen.queryByRole("button", { name: copy.selectBoundary })).not.toBeInTheDocument();
});
it("lookup-only recovery never submits a form", async () => {
  const p = props();
  render(
    <NvkeWorkflowPanel
      {...p}
      recovery={{ requestId: "original", operationKey: "temporal.reset.RotateEtcdEncryptionKey" }}
    />,
    { wrapper }
  );
  fireEvent.click(screen.getByRole("button", { name: copy.recover }));
  await waitFor(() => expect(p.onRecover).toHaveBeenCalledOnce());
  expect(p.onRequest).not.toHaveBeenCalled();
});
it.each(["[]", '{"clusterID":"wrong"}'])(
  "refuses mismatched native cluster input %s",
  async (raw) => {
    const p = props();
    render(<NvkeWorkflowPanel {...p} />, { wrapper });
    fireEvent.click(screen.getByRole("button", { name: copy.submit }));
    fireEvent.change(screen.getByLabelText(messages.nvke.parameters), { target: { value: raw } });
    fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
    await screen.findByRole("alert");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(p.onRequest).not.toHaveBeenCalled();
  }
);

it("does not expose an invalid private schedule in renewal confirmation", async () => {
  const p = props();
  render(<NvkeWorkflowPanel {...p} />, { wrapper });
  fireEvent.click(screen.getByRole("button", { name: copy.submit }));
  fireEvent.change(screen.getByLabelText(messages.nvke.parameters), {
    target: {
      value: JSON.stringify({
        clusterID: p.clusterId,
        thresholdDays: 120,
        scheduledFor: "2030-01-01 (PRIVATE_SCHEDULE)",
      }),
    },
  });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  expect(await screen.findByRole("alertdialog")).not.toHaveTextContent("PRIVATE_SCHEDULE");
});

it("allows receipt-only recovery when execution capability is unavailable", async () => {
  const p = props();
  render(
    <NvkeWorkflowPanel
      {...p}
      canManage={false}
      canInspect={false}
      canRecover={true}
      recovery={{ requestId: "original", operationKey: "temporal.RenewK8sCertsV2" }}
    />,
    { wrapper }
  );
  fireEvent.click(screen.getByRole("button", { name: copy.recover }));
  await waitFor(() => expect(p.onRecover).toHaveBeenCalledOnce());
  expect(p.onRequest).not.toHaveBeenCalled();
});
it("requires inspection before choosing node retirement", async () => {
  const p = props();
  render(<NvkeWorkflowPanel {...p} retirementEligible={false} />, { wrapper });
  fireEvent.click(screen.getByRole("button", { name: copy.submit }));
  fireEvent.click(screen.getByRole("combobox"));
  expect(await screen.findByRole("option", { name: "RetireLostEtcdRotationNode" })).toHaveAttribute(
    "data-disabled"
  );
  expect(p.onRequest).not.toHaveBeenCalled();
});
