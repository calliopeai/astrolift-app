import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import {
  NvkeResourceActionPanel,
  type NvkeResourceActionPanelProps,
} from "./NvkeResourceActionPanel";
import { ACTIONS, OPERATION, SITE, SITE_DETAIL } from "./fixtures";

const Context = ({ children }: { children: ReactNode }) => (
  <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
    {children}
  </NextIntlClientProvider>
);
const action = {
  ...ACTIONS[1],
  key: "updateCluster",
  verb: "PATCH",
  path: "/clusters/{clusterID}",
};
const raw = '{"body":{"controlPlaneConfig":{"desiredCount":9007199254740993}}}';
const props = (): NvkeResourceActionPanelProps => ({
  siteName: SITE.name,
  siteVersion: SITE.version,
  target: "reviewed-cluster",
  actions: [action],
  selectedKey: action.key,
  access: SITE_DETAIL.access,
  sourceReady: true,
  pending: false,
  error: null,
  recovery: null,
  schemaText: "{}",
  editor: {
    id: "template-1",
    siteVersion: SITE.version,
    operationKey: action.key,
    parametersText: raw,
    preparationActionKey: null,
    concurrency: "observed_only",
    source: { operationId: OPERATION.id, version: 21, resourceKey: "signed-key" },
  },
  onSelect: vi.fn(),
  onLoad: vi.fn(async () => false),
  onRefresh: vi.fn(),
  onPrepare: vi.fn(async () => false),
  onRecover: vi.fn(async () => false),
  onRequest: vi.fn(async () => false),
});

it("requires explicit confirmation before loading sensitive native edit content", async () => {
  const value = { ...props(), editor: null };
  render(<NvkeResourceActionPanel {...value} />, { wrapper: Context });
  expect(value.onLoad).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.loadEdit }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent(messages.nvke.loadEditHelp);
  expect(value.onLoad).not.toHaveBeenCalled();
  fireEvent.click(within(dialog).getByRole("button", { name: messages.nvke.loadEdit }));
  await waitFor(() => expect(value.onLoad).toHaveBeenCalledOnce());
});

it("reviews the actual target and site version while preserving exact native text", async () => {
  const value = props();
  render(<NvkeResourceActionPanel {...value} />, { wrapper: Context });
  expect(screen.getByRole("note")).toHaveTextContent(messages.nvke.observedConcurrencyHelp);
  expect(screen.getByLabelText(messages.nvke.parameters)).toHaveValue(raw);
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent("reviewed-cluster");
  expect(dialog).toHaveTextContent("site version 7");
  expect(value.onRequest).not.toHaveBeenCalled();
  fireEvent.click(within(dialog).getByRole("button", { name: messages.nvke.submitRequest }));
  await waitFor(() => expect(value.onRequest).toHaveBeenCalledOnce());
  expect(value.onRequest).toHaveBeenCalledWith({
    operationKey: action.key,
    timeoutSeconds: 7200,
    parametersText: raw,
  });
});

it.each(["permission", "site-version"])(
  "blocks confirmation if %s changes during review",
  async (change) => {
    const value = props();
    const view = render(<NvkeResourceActionPanel {...value} />, { wrapper: Context });
    fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
    const dialog = await screen.findByRole("alertdialog");
    view.rerender(
      <NvkeResourceActionPanel
        {...value}
        {...(change === "permission"
          ? { access: { read: true, manage: false, admin: false, loading: false } }
          : { siteVersion: 8 })}
      />
    );
    const confirm = within(dialog).getByRole("button", { name: messages.nvke.submitRequest });
    expect(confirm).toBeDisabled();
    fireEvent.click(confirm);
    expect(value.onRequest).not.toHaveBeenCalled();
  }
);

it("resets the draft and review if the resource source changes", async () => {
  const value = props();
  const view = render(<NvkeResourceActionPanel {...value} />, { wrapper: Context });
  fireEvent.change(screen.getByLabelText(messages.nvke.parameters), {
    target: { value: '{"body":{"privateDraft":true}}' },
  });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  await screen.findByRole("alertdialog");
  view.rerender(
    <NvkeResourceActionPanel
      {...value}
      editor={{ ...value.editor!, source: { ...value.editor!.source, version: 22 } }}
    />
  );
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  expect(screen.getByLabelText(messages.nvke.parameters)).toHaveValue(raw);
  expect(value.onRequest).not.toHaveBeenCalled();
});
