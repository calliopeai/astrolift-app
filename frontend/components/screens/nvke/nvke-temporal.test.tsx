import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";
import messages from "@/messages/en.json";
import { NvkeTemporalPanel, type NvkeTemporalPanelProps } from "./NvkeTemporalPanel";
import { TEMPORAL_AUTHORITY } from "./NvkeTemporalPanel.stories";
import { SITE } from "./fixtures";
const wrapper = ({ children }: { children: ReactNode }) => (
  <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
    {children}
  </NextIntlClientProvider>
);
const props = (): NvkeTemporalPanelProps => ({
  site: SITE,
  authority: TEMPORAL_AUTHORITY,
  canManage: true,
  pending: false,
  loading: false,
  error: null,
  onRefresh: vi.fn(),
  onConfigure: vi.fn(async () => true),
  onAction: vi.fn(async () => true),
});
it("submits replacement credentials once while keeping them out of review and clearing the draft", async () => {
  const p = props();
  render(<NvkeTemporalPanel {...p} />, { wrapper });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.configure }));
  fireEvent.click(screen.getByRole("combobox"));
  fireEvent.click(await screen.findByRole("option", { name: messages.nvke.replace }));
  fireEvent.change(screen.getByLabelText(messages.nvke.temporalApiKey), {
    target: { value: "PRIVATE_NATIVE_KEY" },
  });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  const review = await screen.findByRole("alertdialog");
  expect(review).not.toHaveTextContent("PRIVATE_NATIVE_KEY");
  fireEvent.click(
    within(review).getByRole("button", { name: messages.shared.confirmation.confirm })
  );
  await waitFor(() => expect(p.onConfigure).toHaveBeenCalledOnce());
  expect(p.onConfigure).toHaveBeenCalledWith(
    expect.objectContaining({ apiKey: "PRIVATE_NATIVE_KEY", credentialMode: "replace" })
  );
  await waitFor(() =>
    expect(screen.queryByDisplayValue("PRIVATE_NATIVE_KEY")).not.toBeInTheDocument()
  );
});
it("reviews exact native authority and target before disabling or removing", async () => {
  const p = props();
  render(<NvkeTemporalPanel {...p} />, { wrapper });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.temporalRemove }));
  const review = screen.getByRole("alertdialog");
  expect(review).toHaveTextContent(`site version ${SITE.version}`);
  expect(review).toHaveTextContent(`native authority version ${TEMPORAL_AUTHORITY.version}`);
  expect(review).toHaveTextContent(TEMPORAL_AUTHORITY.namespace);
  expect(review).toHaveTextContent(TEMPORAL_AUTHORITY.taskQueue);
  expect(p.onAction).not.toHaveBeenCalled();
});
it("read-only, failed and pending metadata cannot configure native authority", () => {
  for (const state of [
    { canManage: false },
    { error: "Refresh metadata" },
    { pending: true },
    { loading: true },
  ]) {
    const p = { ...props(), ...state };
    const view = render(<NvkeTemporalPanel {...p} />, { wrapper });
    const configure = screen.queryByRole("button", { name: messages.nvke.configure });
    if (configure) expect(configure).toBeDisabled();
    expect(p.onConfigure).not.toHaveBeenCalled();
    view.unmount();
  }
});
it("site authority changes discard an open private draft and its review", async () => {
  const p = props();
  const view = render(<NvkeTemporalPanel {...p} />, { wrapper });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.configure }));
  fireEvent.change(screen.getByLabelText(messages.nvke.temporalAddress), {
    target: { value: "changed-native.test:7233" },
  });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.reviewRequest }));
  await waitFor(() =>
    expect(screen.getByRole("alertdialog")).toHaveTextContent("changed-native.test:7233")
  );
  view.rerender(<NvkeTemporalPanel {...p} site={{ ...SITE, version: SITE.version + 1 }} />);
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  expect(screen.queryByDisplayValue("changed-native.test:7233")).not.toBeInTheDocument();
  expect(p.onConfigure).not.toHaveBeenCalled();
});
