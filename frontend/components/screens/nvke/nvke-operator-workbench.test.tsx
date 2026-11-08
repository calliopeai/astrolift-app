import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import { nativeTextDigest } from "@/lib/nvke/recovery";
import { operatorTemplateText, operatorContractSchema } from "@/lib/nvke/operator-contract";
import { NvkeOperatorWorkbench, type NvkeOperatorWorkbenchProps } from "./NvkeOperatorWorkbench";
import { OPERATOR_AGENT_AUTHORITY } from "./NvkeOperatorAgentPanel.stories";
import { SITE } from "./fixtures";
import fixture from "./operator-contract.fixture.json";

const wrapper = ({ children }: { children: ReactNode }) => (
  <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
    {children}
  </NextIntlClientProvider>
);
const w = messages.nvke.operatorWorkbench;
const props = (): NvkeOperatorWorkbenchProps => ({
  scopeKey: "operator-visit",
  site: SITE,
  authority: OPERATOR_AGENT_AUTHORITY,
  contract: operatorContractSchema.parse(fixture),
  loading: false,
  pending: false,
  error: null,
  canManage: true,
  canRecover: false,
  recovery: null,
  onRequest: vi.fn(async () => true),
  onRecover: vi.fn(async () => false),
  onRefresh: vi.fn(),
});
const text = (command = "version", options: Record<string, unknown> = {}) =>
  JSON.stringify(
    {
      format: 1,
      revision: SITE.upstreamRevision,
      siteId: SITE.id,
      command,
      positionals: [],
      options,
      files: { "private-input": "PRIVATE_NATIVE_TEXT\n18446744073709551615" },
    },
    null,
    2
  );
async function select(command: string) {
  fireEvent.click(screen.getByRole("combobox"));
  fireEvent.click(await screen.findByRole("option", { name: command }));
}
const confirm = (dialog: HTMLElement) =>
  fireEvent.click(within(dialog).getByRole("button", { name: messages.nvke.submitRequest }));

it("exposes all pinned commands and every declared option without a private draft", async () => {
  const p = props();
  render(<NvkeOperatorWorkbench {...p} />, { wrapper });
  fireEvent.click(screen.getByRole("combobox"));
  expect(await screen.findAllByRole("option")).toHaveLength(17);
  for (const command of fixture.commands)
    expect(screen.getByRole("option", { name: command.key })).toBeInTheDocument();
  for (const command of p.contract!.commands) {
    const template = JSON.parse(operatorTemplateText(command, SITE.id, SITE.upstreamRevision));
    expect(template.positionals).toHaveLength(command.positionals.length);
    expect(Object.keys(template.options)).toEqual(
      command.options.filter((row) => row.required).map((row) => row.name)
    );
  }
});

it("reviews only immutable public pins and the exact UTF-8 hash while clearing private input", async () => {
  const p = props(),
    original = text();
  render(<NvkeOperatorWorkbench {...p} />, { wrapper });
  fireEvent.change(screen.getByLabelText(w.request), { target: { value: original } });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).not.toHaveTextContent("PRIVATE_NATIVE_TEXT");
  expect(dialog).toHaveTextContent(await nativeTextDigest(original));
  expect(dialog).toHaveTextContent(OPERATOR_AGENT_AUTHORITY.instanceId);
  expect(dialog).toHaveTextContent(OPERATOR_AGENT_AUTHORITY.bundleManifestSha256);
  expect(screen.queryByDisplayValue(original)).not.toBeInTheDocument();
  fireEvent.change(document.querySelector("#nvke-tooling-request")!, {
    target: { value: "changed behind review" },
  });
  confirm(dialog);
  await waitFor(() => expect(p.onRequest).toHaveBeenCalledOnce());
  expect(p.onRequest).toHaveBeenCalledWith({
    operationKey: "tooling.version",
    requestText: original,
    timeoutSeconds: 7200,
    acknowledgeInfrastructureChange: false,
  });
});

it("discards the private snapshot on cancellation", async () => {
  const p = props();
  render(<NvkeOperatorWorkbench {...p} />, { wrapper });
  fireEvent.change(screen.getByLabelText(w.request), { target: { value: text() } });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  const dialog = await screen.findByRole("alertdialog");
  fireEvent.click(
    within(dialog).getByRole("button", { name: messages.shared.confirmation.cancel })
  );
  await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  expect(screen.getByLabelText(w.request)).toHaveValue("");
  expect(p.onRequest).not.toHaveBeenCalled();
});

it.each(["scope", "site", "agent", "role", "pending"])(
  "discards private review when %s changes",
  async (change) => {
    const p = props(),
      r = render(<NvkeOperatorWorkbench {...p} />, { wrapper });
    fireEvent.change(screen.getByLabelText(w.request), { target: { value: text() } });
    fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
    await screen.findByRole("alertdialog");
    r.rerender(
      <NvkeOperatorWorkbench
        {...p}
        scopeKey={change === "scope" ? "other-visit" : p.scopeKey}
        site={change === "site" ? { ...SITE, version: SITE.version + 1 } : SITE}
        authority={change === "agent" ? { ...OPERATOR_AGENT_AUTHORITY, version: 99 } : p.authority}
        canManage={change !== "role"}
        pending={change === "pending"}
      />
    );
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue(text())).not.toBeInTheDocument();
    expect(p.onRequest).not.toHaveBeenCalled();
  }
);

it("requires infrastructure acknowledgement and distinguishes an explicit dry-run", async () => {
  const p = props();
  render(<NvkeOperatorWorkbench {...p} />, { wrapper });
  await select("bootstrap.site");
  fireEvent.change(screen.getByLabelText(w.request), { target: { value: text("bootstrap.site") } });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  await screen.findByRole("alert");
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent(w.effect);
  confirm(dialog);
  await waitFor(() => expect(p.onRequest).toHaveBeenCalled());
  expect(p.onRequest).toHaveBeenCalledWith(
    expect.objectContaining({ acknowledgeInfrastructureChange: true })
  );
});

it("permits dry-run review without an infrastructure acknowledgement", async () => {
  const p = props();
  render(<NvkeOperatorWorkbench {...p} />, { wrapper });
  await select("bootstrap.site");
  fireEvent.change(screen.getByLabelText(w.request), {
    target: { value: text("bootstrap.site", { "dry-run": true }) },
  });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent(w.utility);
  confirm(dialog);
  await waitFor(() => expect(p.onRequest).toHaveBeenCalled());
});

it.each(["null", "[]", "{", text("bootstrap.site"), text().replace(SITE.id, "different-site")])(
  "refuses a mismatched native envelope",
  async (requestText) => {
    const p = props();
    render(<NvkeOperatorWorkbench {...p} />, { wrapper });
    fireEvent.change(screen.getByLabelText(w.request), { target: { value: requestText } });
    fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
    await screen.findByRole("alert");
    expect(p.onRequest).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  }
);

it("catches callback diagnostics and blocks new writes after an unconfirmed request", async () => {
  const p = props();
  p.onRequest = vi.fn(async () => {
    throw new Error("PRIVATE_DIAGNOSTICS");
  });
  render(<NvkeOperatorWorkbench {...p} />, { wrapper });
  fireEvent.change(screen.getByLabelText(w.request), { target: { value: text() } });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  confirm(await screen.findByRole("alertdialog"));
  await screen.findByRole("alert");
  expect(screen.queryByText("PRIVATE_DIAGNOSTICS")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: messages.nvke.reviewRequest })).toBeDisabled();
  expect(screen.getByLabelText(w.request)).toHaveValue("");
});

it("imports exact private UTF-8 text, selects its command, and clears the file input", async () => {
  const p = props();
  render(<NvkeOperatorWorkbench {...p} />, { wrapper });
  const original = text("inspect.sitespec"),
    file = new File([original], "private.json", { type: "application/json" });
  file.arrayBuffer = async () => new TextEncoder().encode(original).buffer;
  const input = screen.getByLabelText(w.import);
  await act(async () => fireEvent.change(input, { target: { files: [file] } }));
  expect(screen.getByLabelText(w.request)).toHaveValue(original);
  expect(screen.getByRole("combobox")).toHaveTextContent("inspect.sitespec");
  expect(input).toHaveValue("");
});

it("allows receipt lookup after authority removal without exposing admission", async () => {
  const p = props();
  p.canManage = false;
  p.canRecover = true;
  p.authority = null;
  p.recovery = {
    requestId: "123e4567-e89b-12d3-a456-426614174093",
    operationKey: "tooling.version",
    timeoutSeconds: 600,
    expectedSiteVersion: 1,
    authorityVersion: 1,
    acknowledgeInfrastructureChange: false,
  };
  render(<NvkeOperatorWorkbench {...p} />, { wrapper });
  fireEvent.click(screen.getByRole("button", { name: w.recover }));
  await waitFor(() => expect(p.onRecover).toHaveBeenCalledOnce());
  expect(p.onRequest).not.toHaveBeenCalled();
  expect(
    screen.queryByRole("button", { name: messages.nvke.retryRequest })
  ).not.toBeInTheDocument();
});
