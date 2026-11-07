import { fireEvent, render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import messages from "@/messages/en.json";
import { NvkeMaintenancePanel } from "./NvkeMaintenancePanel";
import { MAINTENANCE_OBSERVATION } from "./fixtures";
const wrapper = ({ children }: { children: ReactNode }) => (
  <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
    {children}
  </NextIntlClientProvider>
);
const t = messages.nvke.maintenance;
it("shows failed renewal and retained ownership independently of provisioning", () => {
  const refresh = vi.fn();
  render(
    <NvkeMaintenancePanel
      observation={MAINTENANCE_OBSERVATION}
      loading={false}
      error={null}
      onRefresh={refresh}
    />,
    { wrapper }
  );
  const cycle = screen.getByRole("heading", { name: t.renewal }).closest("section")!;
  expect(within(cycle).getByText(t.state).nextElementSibling).toHaveTextContent(t.values.failed);
  expect(within(cycle).getByText(t.retained).nextElementSibling).toHaveTextContent(t.yes);
  expect(within(cycle).getByText(t.forced).nextElementSibling).toHaveTextContent(t.no);
  expect(within(cycle).getByText(t.outcome).nextElementSibling).toHaveTextContent(t.notReported);
  expect(screen.getByRole("note")).toHaveTextContent(MAINTENANCE_OBSERVATION.observedAt!);
  expect(screen.getByText(t.help)).toHaveTextContent(
    "Certificate expiry requires separate inspection data"
  );
  expect(screen.getAllByRole("button")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.refresh }));
  expect(refresh).toHaveBeenCalledOnce();
});
it("keeps absent cycles and absent scheduling evidence distinct from success", () => {
  render(
    <NvkeMaintenancePanel
      observation={{
        ...MAINTENANCE_OBSERVATION,
        renewal: null,
        etcdKeyRotation: null,
        nextCertificateRenewalDue: null,
        renewalScheduleOverdueAtObservation: null,
        mutationLockReported: null,
      }}
      loading={false}
      error={null}
      onRefresh={() => {}}
    />,
    { wrapper }
  );
  expect(screen.getByText(t.noRenewal)).toBeInTheDocument();
  expect(screen.getByText(t.noRotation)).toBeInTheDocument();
  expect(screen.getByText(t.nextCheck).nextElementSibling).toHaveTextContent(t.notReported);
  expect(screen.getByText(t.overdue).nextElementSibling).toHaveTextContent(t.notReported);
  expect(screen.getByText(t.lockReported).nextElementSibling).toHaveTextContent(t.notReported);
});
