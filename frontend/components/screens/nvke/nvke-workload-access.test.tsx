import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import messages from "@/messages/en.json";
import { NvkeWorkloadAccessPanel } from "./NvkeWorkloadAccessPanel";
import meta, { ACCESS_BINDING } from "./NvkeWorkloadAccessPanel.stories";
const wrapper = ({ children }: { children: ReactNode }) => (
  <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
    {children}
  </NextIntlClientProvider>
);
it("confirms the exact source, native UUID and reviewed site before enrollment", async () => {
  const onEnroll = vi.fn().mockResolvedValue(true);
  render(<NvkeWorkloadAccessPanel {...meta.args} onEnroll={onEnroll} />, { wrapper });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.workloadAccess.enroll }));
  const dialog = screen.getByRole("alertdialog");
  expect(dialog).toHaveTextContent(ACCESS_BINDING.clusterId);
  expect(dialog).toHaveTextContent(meta.args.site.name);
  expect(dialog).toHaveTextContent(meta.args.candidate.sourceOperationId);
  expect(dialog).toHaveTextContent("observation version 9");
  expect(onEnroll).not.toHaveBeenCalled();
  fireEvent.click(
    within(dialog).getByRole("button", { name: messages.shared.confirmation.confirm })
  );
  await waitFor(() => expect(onEnroll).toHaveBeenCalledExactlyOnceWith(meta.args.candidate));
});
it("invalidates an open enrollment review when its source changes", () => {
  const onEnroll = vi.fn();
  const view = render(<NvkeWorkloadAccessPanel {...meta.args} onEnroll={onEnroll} />, { wrapper });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.workloadAccess.enroll }));
  view.rerender(
    <NvkeWorkloadAccessPanel
      {...meta.args}
      candidate={{ ...meta.args.candidate, sourceVersion: 10 }}
      onEnroll={onEnroll}
    />
  );
  const confirm = within(screen.getByRole("alertdialog")).getByRole("button", {
    name: messages.shared.confirmation.confirm,
  });
  expect(confirm).toBeDisabled();
  fireEvent.click(confirm);
  expect(onEnroll).not.toHaveBeenCalled();
});

it("closes an unconfirmed review so the grant-list recovery controls remain accessible", async () => {
  const onEnroll = vi.fn().mockResolvedValue(false);
  const view = render(<NvkeWorkloadAccessPanel {...meta.args} onEnroll={onEnroll} />, { wrapper });
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.workloadAccess.enroll }));
  fireEvent.click(
    within(screen.getByRole("alertdialog")).getByRole("button", {
      name: messages.shared.confirmation.confirm,
    })
  );
  await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  view.rerender(
    <NvkeWorkloadAccessPanel
      {...meta.args}
      onEnroll={onEnroll}
      error={messages.nvke.workloadAccess.writeFailed}
    />
  );
  expect(screen.getByRole("alert")).toHaveTextContent(messages.nvke.workloadAccess.writeFailed);
  expect(screen.getByRole("button", { name: messages.nvke.refresh })).toBeEnabled();
  expect(onEnroll).toHaveBeenCalledOnce();
});
it("reviews revocation as withdrawing one imported grant", async () => {
  const onRevoke = vi.fn().mockResolvedValue(true);
  render(<NvkeWorkloadAccessPanel {...meta.args} candidate={null} onRevoke={onRevoke} />, {
    wrapper,
  });
  const row = screen.getByRole("row", { name: new RegExp(ACCESS_BINDING.clusterId) });
  fireEvent.pointerDown(within(row).getByRole("button"), { button: 0, ctrlKey: false });
  fireEvent.click(screen.getByRole("menuitem", { name: messages.nvke.workloadAccess.revoke }));
  const dialog = screen.getByRole("alertdialog");
  expect(dialog).toHaveTextContent("Independent native CRD or Astro create-receipt");
  expect(dialog).toHaveTextContent(ACCESS_BINDING.id);
  fireEvent.click(
    within(dialog).getByRole("button", { name: messages.shared.confirmation.confirm })
  );
  await waitFor(() =>
    expect(onRevoke).toHaveBeenCalledExactlyOnceWith({
      id: ACCESS_BINDING.id,
      version: ACCESS_BINDING.version,
      clusterId: ACCESS_BINDING.clusterId,
      siteVersion: meta.args.site.version,
    })
  );
});
