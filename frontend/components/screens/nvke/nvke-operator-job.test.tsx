import { fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import { NvkeOperatorJobPanel } from "./NvkeOperatorJobPanel";
import { OPERATOR_JOB_PROPS } from "./NvkeOperatorJobPanel.stories";
const wrapper = ({ children }: { children: ReactNode }) => (
  <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
    {children}
  </NextIntlClientProvider>
);
const j = messages.nvke.operatorJob;
function props() {
  return {
    ...OPERATOR_JOB_PROPS,
    onDownload: vi.fn(async () => true),
    onReconcile: vi.fn(async () => true),
    onStop: vi.fn(async () => true),
    onLoad: vi.fn(),
    onRefresh: vi.fn(),
    onCancelDownload: vi.fn(),
  };
}
const download = () => fireEvent.click(screen.getAllByRole("button", { name: j.download })[0]);
it("reviews exact original source, artifact hash and memory budget before private download", async () => {
  const p = props();
  render(<NvkeOperatorJobPanel {...p} />, { wrapper });
  fireEvent.change(screen.getByLabelText(j.maximum), { target: { value: "128" } });
  download();
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent(p.operation.id);
  expect(dialog).toHaveTextContent(p.authority!.instanceId);
  expect(dialog).toHaveTextContent("a".repeat(64));
  expect(dialog).toHaveTextContent("128 MiB");
  fireEvent.change(document.querySelector("#nvke-artifact-budget")!, { target: { value: "1" } });
  fireEvent.click(
    within(dialog).getByRole("button", { name: messages.shared.confirmation.confirm })
  );
  await waitFor(() =>
    expect(p.onDownload).toHaveBeenCalledExactlyOnceWith(p.table.rows[0], 128 * 1048576)
  );
});
it("asks for explicit original-receipt loading without automatically calling an API", () => {
  const p = props();
  render(<NvkeOperatorJobPanel {...p} receipt={null} loaded={false} />, { wrapper });
  expect(p.onLoad).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: j.load }));
  expect(p.onLoad).toHaveBeenCalledOnce();
});
it("reviews read-only reconciliation without native replay claims", async () => {
  const p = props();
  render(
    <NvkeOperatorJobPanel
      {...p}
      operation={{ ...p.operation, status: "UNCERTAIN" }}
      canReconcile
    />,
    { wrapper }
  );
  fireEvent.click(screen.getByRole("button", { name: j.reconcile }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent(j.reconcileHelp);
  expect(dialog).toHaveTextContent(p.operation.id);
  expect(dialog).toHaveTextContent(p.authority!.bundleManifestSha256);
  fireEvent.click(
    within(dialog).getByRole("button", { name: messages.shared.confirmation.confirm })
  );
  await waitFor(() => expect(p.onReconcile).toHaveBeenCalledOnce());
  expect(p.onDownload).not.toHaveBeenCalled();
});
it("reviews original stop or fencing separately from read-only reconciliation and download", async () => {
  const p = props();
  render(
    <NvkeOperatorJobPanel
      {...p}
      operation={{ ...p.operation, status: "UNCERTAIN" }}
      canStop
      canReconcile
    />,
    { wrapper }
  );
  fireEvent.click(screen.getByRole("button", { name: j.stop }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent(j.stopHelp);
  expect(dialog).toHaveTextContent(p.operation.id);
  expect(dialog).toHaveTextContent(p.authority!.instanceId);
  expect(dialog).toHaveTextContent(p.authority!.bundleManifestSha256);
  expect(p.onStop).not.toHaveBeenCalled();
  fireEvent.click(
    within(dialog).getByRole("button", { name: messages.shared.confirmation.confirm })
  );
  await waitFor(() => expect(p.onStop).toHaveBeenCalledOnce());
  expect(p.onReconcile).not.toHaveBeenCalled();
  expect(p.onDownload).not.toHaveBeenCalled();
});
it("requires a loaded reviewed original source before stopping an uncertain job", () => {
  const p = props();
  render(
    <NvkeOperatorJobPanel
      {...p}
      operation={{ ...p.operation, status: "UNCERTAIN" }}
      loaded={false}
      receipt={null}
    />,
    { wrapper }
  );
  expect(screen.getByRole("button", { name: j.stop })).toBeDisabled();
  expect(p.onStop).not.toHaveBeenCalled();
});
it.each(["scope", "source", "operation", "role", "pending", "eligibility"])(
  "invalidates an open stop review after %s changes",
  async (change) => {
    const p = props(),
      original = {
        ...p,
        operation: { ...p.operation, status: "UNCERTAIN" as const },
        canStop: true,
      },
      r = render(<NvkeOperatorJobPanel {...original} />, { wrapper });
    fireEvent.click(screen.getByRole("button", { name: j.stop }));
    await screen.findByRole("alertdialog");
    r.rerender(
      <NvkeOperatorJobPanel
        {...original}
        scopeKey={change === "scope" ? "other" : p.scopeKey}
        authority={change === "source" ? { ...p.authority!, version: 99 } : p.authority}
        operation={
          change === "operation" ? { ...original.operation, version: 99 } : original.operation
        }
        canManage={change !== "role"}
        pending={change === "pending"}
        canStop={change !== "eligibility"}
      />
    );
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(p.onStop).not.toHaveBeenCalled();
  }
);
it("holds an unconfirmed stop until explicit refresh without leaking callback diagnostics", async () => {
  const p = props();
  p.onStop.mockRejectedValue(new Error("PRIVATE_STOP_DIAGNOSTIC"));
  render(
    <NvkeOperatorJobPanel
      {...p}
      operation={{ ...p.operation, status: "UNCERTAIN" }}
      canStop
      canReconcile
    />,
    { wrapper }
  );
  fireEvent.click(screen.getByRole("button", { name: j.stop }));
  fireEvent.click(
    within(await screen.findByRole("alertdialog")).getByRole("button", {
      name: messages.shared.confirmation.confirm,
    })
  );
  await screen.findByRole("alert");
  expect(screen.queryByText("PRIVATE_STOP_DIAGNOSTIC")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: j.stop })).toBeDisabled();
  expect(screen.getByRole("button", { name: j.reconcile })).toBeDisabled();
  expect(p.onStop).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.refresh }));
  expect(p.onRefresh).toHaveBeenCalledOnce();
});
it.each(["scope", "source", "operation", "role", "pending"])(
  "invalidates an open artifact review after %s changes",
  async (change) => {
    const p = props(),
      r = render(<NvkeOperatorJobPanel {...p} />, { wrapper });
    download();
    await screen.findByRole("alertdialog");
    r.rerender(
      <NvkeOperatorJobPanel
        {...p}
        scopeKey={change === "scope" ? "other" : p.scopeKey}
        authority={change === "source" ? { ...p.authority!, version: 99 } : p.authority}
        operation={change === "operation" ? { ...p.operation, version: 99 } : p.operation}
        canManage={change !== "role"}
        pending={change === "pending"}
      />
    );
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(p.onDownload).not.toHaveBeenCalled();
  }
);
it("refuses invalid budgets and an artifact larger than the reviewed limit", async () => {
  const p = props();
  render(<NvkeOperatorJobPanel {...p} />, { wrapper });
  for (const budget of ["0", "257", "1.5"]) {
    fireEvent.change(screen.getByLabelText(j.maximum), { target: { value: budget } });
    download();
    await screen.findByRole("alert");
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  }
  expect(p.onDownload).not.toHaveBeenCalled();
});
it("does not surface raw callback diagnostics or silently retry an unconfirmed download", async () => {
  const p = props();
  p.onDownload.mockRejectedValue(new Error("PRIVATE_CALLBACK_DIAGNOSTIC"));
  render(<NvkeOperatorJobPanel {...p} />, { wrapper });
  download();
  fireEvent.click(
    within(await screen.findByRole("alertdialog")).getByRole("button", {
      name: messages.shared.confirmation.confirm,
    })
  );
  await screen.findByRole("alert");
  expect(screen.queryByText("PRIVATE_CALLBACK_DIAGNOSTIC")).not.toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: j.download })[0]).toBeDisabled();
  expect(p.onDownload).toHaveBeenCalledOnce();
});
it("exposes cancellation only for the local active transfer", () => {
  const p = props();
  render(<NvkeOperatorJobPanel {...p} pending progress={{ completed: 1, total: 5 }} />, {
    wrapper,
  });
  fireEvent.click(screen.getByRole("button", { name: j.cancelDownload }));
  expect(p.onCancelDownload).toHaveBeenCalledOnce();
  expect(p.onReconcile).not.toHaveBeenCalled();
  expect(p.onDownload).not.toHaveBeenCalled();
});
