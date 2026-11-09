import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import messages from "@/messages/en.json";
import { NvkeNativePanel } from "./NvkeNativePanel";
import { NvkeOperationScreen } from "./NvkeOperationScreen";
import { NvkeSitesScreen } from "./NvkeSitesScreen";
import { NvkeResourceScreen } from "./NvkeResourceScreen";
import { INSTALL_TREE_PROPS } from "./NvkeOperatorInstallPlanTreePanel.stories";
import { INSTALL_CHARTS_PROPS } from "./NvkeOperatorInstallPlanChartsPanel.stories";
import {
  ACTIONS,
  COLLECTION,
  INVENTORY_ITEM,
  OPERATION_DETAIL,
  SITE_DETAIL,
  SITE,
  SITES,
} from "./fixtures";

const Context = ({ children }: { children: ReactNode }) => (
  <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
    {children}
  </NextIntlClientProvider>
);

it.each(["pending", "read-only", "error"])(
  "blocks chart actions on an operation screen that is %s",
  (mode) => {
    const onReview = vi.fn().mockResolvedValue(true);
    render(
      <NvkeOperationScreen
        {...OPERATION_DETAIL}
        operation={INSTALL_CHARTS_PROPS.operation}
        operatorInstallPlanCharts={{ ...INSTALL_CHARTS_PROPS, onReview }}
        pending={mode === "pending"}
        canAct={mode !== "read-only"}
        error={mode === "error" ? "Unavailable" : null}
      />,
      { wrapper: Context }
    );
    const button = screen.getByRole("button", { name: messages.nvke.installCharts.review });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(onReview).not.toHaveBeenCalled();
    if (mode !== "pending")
      expect(
        screen.queryByText(INSTALL_CHARTS_PROPS.artifacts!.artifactDigest)
      ).not.toBeInTheDocument();
  }
);

it("shows exact metadata and requires confirmation before downloading a native resource document", async () => {
  const onDownload = vi.fn().mockResolvedValue(undefined);
  render(
    <NvkeResourceScreen
      siteId={SITE.id}
      siteName={SITE.name}
      operationId={OPERATION_DETAIL.operation!.id}
      collection={COLLECTION}
      item={INVENTORY_ITEM}
      observedAt="2026-11-07T12:03:00Z"
      loading={false}
      pending={false}
      error={null}
      canRead
      onRefresh={() => {}}
      onDownload={onDownload}
    />,
    { wrapper: Context }
  );
  expect(screen.getByText("9007199254740993")).toBeInTheDocument();
  expect(screen.getByText("Ready: True")).toBeInTheDocument();
  expect(screen.getByRole("note")).toHaveTextContent(messages.nvke.historicalHelp);
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.nativeDocument }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent(messages.nvke.downloadHelp);
  expect(onDownload).not.toHaveBeenCalled();
  fireEvent.click(within(dialog).getByRole("button", { name: messages.nvke.nativeDocument }));
  await waitFor(() => expect(onDownload).toHaveBeenCalledOnce());
});

it("keeps large native numbers as the original text and requires explicit mutation confirmation", async () => {
  const onRequest = vi.fn().mockResolvedValue(false);
  render(<NvkeNativePanel {...SITE_DETAIL} actions={[ACTIONS[1]]} onRequest={onRequest} />, {
    wrapper: Context,
  });
  const raw = '{"body":{"name":"cluster-a","desiredNodeCount":9007199254740993}}';
  fireEvent.change(screen.getByLabelText(messages.nvke.parameters), { target: { value: raw } });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  const dialog = await screen.findByRole("alertdialog");
  expect(onRequest).not.toHaveBeenCalled();
  expect(within(dialog).getByText(/site version 7/)).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: messages.nvke.submitRequest }));
  await waitFor(() => expect(onRequest).toHaveBeenCalledTimes(1));
  expect(onRequest.mock.calls[0][0].parametersText).toBe(raw);
  expect(screen.getByRole("alertdialog")).toBeInTheDocument();
});

it("permission loss while reviewing blocks the pending mutation", async () => {
  const onRequest = vi.fn().mockResolvedValue(true);
  const view = render(
    <NvkeNativePanel {...SITE_DETAIL} actions={[ACTIONS[1]]} onRequest={onRequest} />,
    { wrapper: Context }
  );
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  await screen.findByRole("alertdialog");
  view.rerender(
    <NvkeNativePanel
      {...SITE_DETAIL}
      actions={[ACTIONS[1]]}
      access={{ read: true, manage: false, admin: false, loading: false }}
      onRequest={onRequest}
    />
  );
  const confirm = screen.getByRole("button", { name: messages.nvke.submitRequest });
  expect(confirm).toBeDisabled();
  fireEvent.click(confirm);
  expect(onRequest).not.toHaveBeenCalled();
});

describe.each([
  "read-only",
  "no-interface",
  "disabled-site",
  "contract-drift",
  "pending",
  "loading-access",
])("native admission: %s", (condition) => {
  it("blocks both the visible button and a forged submit event", async () => {
    const onRequest = vi.fn().mockResolvedValue(true);
    const props = { ...SITE_DETAIL, actions: [ACTIONS[1]], onRequest };
    if (condition === "read-only")
      props.access = { read: true, manage: false, admin: false, loading: false };
    if (condition === "no-interface") props.site = { ...props.site!, connections: [] };
    if (condition === "disabled-site") props.site = { ...props.site!, isActive: false };
    if (condition === "contract-drift") props.contractRevision = "changed";
    if (condition === "pending") props.pending = true;
    if (condition === "loading-access") props.access = { ...props.access, loading: true };
    render(<NvkeNativePanel {...props} />, { wrapper: Context });
    const button = screen.getByRole("button", {
      name: props.pending ? messages.nvke.working : messages.nvke.reviewRequest,
    });
    expect(button).toBeDisabled();
    fireEvent.submit(button.closest("form")!);
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(onRequest).not.toHaveBeenCalled();
  });
});

it("recovery freezes the original action and timeout while retaining a place for original input", () => {
  render(
    <NvkeNativePanel
      {...SITE_DETAIL}
      recovery={{
        requestId: "original-request",
        expectedSiteVersion: 4,
        operationKey: ACTIONS[1].key,
        timeoutSeconds: 60,
      }}
    />,
    { wrapper: Context }
  );
  expect(screen.getByRole("combobox")).toBeDisabled();
  expect(screen.getByLabelText(messages.nvke.timeout)).toBeDisabled();
  expect(screen.getByLabelText(messages.nvke.timeout)).toHaveValue(60);
  expect(screen.getByLabelText(messages.nvke.parameters)).not.toBeDisabled();
  expect(screen.getByRole("status")).toHaveTextContent("original-request");
});

it("ordinary metadata never downloads native results and explicit download requires confirmation", async () => {
  const onDownload = vi.fn().mockResolvedValue(undefined);
  render(<NvkeOperationScreen {...OPERATION_DETAIL} onDownload={onDownload} />, {
    wrapper: Context,
  });
  expect(onDownload).not.toHaveBeenCalled();
  expect(screen.getByRole("status")).toHaveTextContent(messages.nvke.acceptedHelp);
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.downloadResult }));
  const dialog = await screen.findByRole("alertdialog");
  expect(onDownload).not.toHaveBeenCalled();
  fireEvent.click(
    within(dialog).getByRole("button", { name: messages.shared.confirmation.confirm })
  );
  await waitFor(() => expect(onDownload).toHaveBeenCalledTimes(1));
});

it("read-only operation metadata blocks result retrieval, cancellation and reconciliation", () => {
  const onCancel = vi.fn(),
    onResume = vi.fn(),
    onDownload = vi.fn();
  render(
    <NvkeOperationScreen
      {...OPERATION_DETAIL}
      operation={{ ...OPERATION_DETAIL.operation!, status: "UNCERTAIN" }}
      canAct={false}
      canResume={false}
      onCancel={onCancel}
      onResume={onResume}
      onDownload={onDownload}
    />,
    { wrapper: Context }
  );
  for (const name of [
    messages.nvke.downloadResult,
    messages.nvke.cancelOperation,
    messages.nvke.reconcile,
  ]) {
    const button = screen.getByRole("button", { name });
    expect(button).toBeDisabled();
    fireEvent.click(button);
  }
  expect(onCancel).not.toHaveBeenCalled();
  expect(onResume).not.toHaveBeenCalled();
  expect(onDownload).not.toHaveBeenCalled();
});

it("site creation remains unavailable until admin authority is established", () => {
  const onCreate = vi.fn().mockResolvedValue(true);
  render(
    <NvkeSitesScreen
      {...SITES}
      access={{ read: true, manage: true, admin: false, loading: false }}
      onCreate={onCreate}
    />,
    { wrapper: Context }
  );
  expect(screen.queryByRole("button", { name: messages.nvke.createSite })).not.toBeInTheDocument();
  expect(onCreate).not.toHaveBeenCalled();
});

it("blocks a creation confirmation after its reviewed site version changes", async () => {
  const onRequest = vi.fn().mockResolvedValue(true);
  const view = render(
    <NvkeNativePanel {...SITE_DETAIL} actions={[ACTIONS[1]]} onRequest={onRequest} />,
    { wrapper: Context }
  );
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent("site version 7");
  view.rerender(
    <NvkeNativePanel
      {...SITE_DETAIL}
      site={{ ...SITE, version: 8 }}
      actions={[ACTIONS[1]]}
      onRequest={onRequest}
    />
  );
  expect(within(dialog).getByRole("button", { name: messages.nvke.submitRequest })).toBeDisabled();
  expect(dialog).toHaveTextContent("site version 7");
  fireEvent.click(within(dialog).getByRole("button", { name: messages.nvke.submitRequest }));
  expect(onRequest).not.toHaveBeenCalled();
});

it.each(["pending", "read-only", "error"])(
  "blocks tree actions when the operation screen is %s",
  (mode) => {
    const onReview = vi.fn().mockResolvedValue(true);
    render(
      <NvkeOperationScreen
        {...OPERATION_DETAIL}
        operation={INSTALL_TREE_PROPS.operation}
        operatorInstallPlanTree={{ ...INSTALL_TREE_PROPS, onReview }}
        pending={mode === "pending"}
        canAct={mode !== "read-only"}
        error={mode === "error" ? "Unavailable" : null}
      />,
      { wrapper: Context }
    );
    const button = screen.getByRole("button", { name: messages.nvke.installTree.review });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(onReview).not.toHaveBeenCalled();
    if (mode !== "pending")
      expect(screen.queryByText(INSTALL_TREE_PROPS.artifacts!.treeDigest)).not.toBeInTheDocument();
  }
);
