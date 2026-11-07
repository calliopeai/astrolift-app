import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import {
  NvkeResourceActionPanel,
  type NvkeResourceActionPanelProps,
} from "./NvkeResourceActionPanel";
import { COLLECTION, ACTIONS, OPERATION, SITE, SITE_DETAIL } from "./fixtures";

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

it("reviews typed SPIRE configuration against the fixed resource before admission", async () => {
  const spire = COLLECTION.actions.find((action) => action.key === "updateClusterSpireServer")!;
  const base = props();
  const value = {
    ...base,
    actions: [spire],
    selectedKey: spire.key,
    editor: {
      ...base.editor!,
      operationKey: spire.key,
      parametersText: '{"body":{"address":"spire.old.test","port":30440}}',
    },
  };
  render(<NvkeResourceActionPanel {...value} />, { wrapper: Context });
  expect(screen.getByLabelText(messages.nvke.spireAddress)).toHaveValue("spire.old.test");
  fireEvent.change(screen.getByLabelText(messages.nvke.spireAddress), {
    target: { value: "spire.new.test" },
  });
  fireEvent.change(screen.getByLabelText(messages.nvke.spirePort), { target: { value: "65536" } });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent(messages.nvke.invalidForm)
  );
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText(messages.nvke.spirePort), { target: { value: "30441" } });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent("reviewed-cluster");
  expect(dialog).toHaveTextContent("updateClusterSpireServer");
  expect(value.onRequest).not.toHaveBeenCalled();
  fireEvent.click(within(dialog).getByRole("button", { name: messages.nvke.submitRequest }));
  await waitFor(() =>
    expect(value.onRequest).toHaveBeenCalledWith({
      operationKey: spire.key,
      timeoutSeconds: 7200,
      parametersText: '{"body":{"address":"spire.new.test","port":30441}}',
    })
  );
});
it("offers bound reads on a resource without mutable actions", async () => {
  const read = COLLECTION.actions.find((action) => action.key === "getClusterControlPlane")!;
  const onRead = vi.fn().mockResolvedValue(true);
  render(
    <NvkeResourceActionPanel
      {...props()}
      actions={[]}
      selectedKey=""
      editor={null}
      reads={[read]}
      readReady
      onRead={onRead}
      access={{ read: true, manage: false, admin: false, loading: false }}
    />,
    { wrapper: Context }
  );
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: read.key }));
  expect(onRead).toHaveBeenCalledWith(read.key);
});
it("confirms leaving a loaded editor before admitting a diagnostic read", async () => {
  const read = COLLECTION.actions.find((action) => action.key === "getClusterSpireServer")!;
  const onRead = vi.fn().mockResolvedValue(true);
  render(<NvkeResourceActionPanel {...props()} reads={[read]} readReady onRead={onRead} />, {
    wrapper: Context,
  });
  fireEvent.click(screen.getByRole("button", { name: read.key }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent(messages.nvke.discardHelp);
  expect(onRead).not.toHaveBeenCalled();
});
