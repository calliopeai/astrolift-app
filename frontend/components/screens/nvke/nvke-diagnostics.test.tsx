import { fireEvent, render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import messages from "@/messages/en.json";
import { fakeController } from "@/components/data-table/fixtures";
import { NvkeDiagnosticsPanel } from "./NvkeDiagnosticsPanel";
import { DIAGNOSTIC_OBSERVATION, DIAGNOSTIC_ROW } from "./fixtures";
const wrapper = ({ children }: { children: ReactNode }) => (
  <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
    {children}
  </NextIntlClientProvider>
);
it("renders exact native counters and distinguishes missing counters from zero", () => {
  render(
    <NvkeDiagnosticsPanel
      observation={{
        ...DIAGNOSTIC_OBSERVATION,
        summary: {
          ...DIAGNOSTIC_OBSERVATION.summary,
          kind: "control_plane",
          counts: [
            { name: "desiredCount", value: "9007199254740993" },
            { name: "readyNodeCount", value: null },
          ],
          flags: [{ name: "recyclingEnabled", value: false }],
          conditions: [{ name: "controlPlaneUpgrading", value: "Unknown" }],
        },
      }}
      table={fakeController({ rows: [] })}
      loading={false}
      error={null}
      onRefresh={() => {}}
    />,
    { wrapper }
  );
  expect(screen.getByText("9007199254740993")).toBeInTheDocument();
  expect(
    screen.getByText(messages.nvke.diagnostics.readyNodeCount).nextElementSibling
  ).toHaveTextContent(messages.nvke.diagnostics.notReported);
  expect(
    screen.getByText(messages.nvke.diagnostics.recyclingEnabled).nextElementSibling
  ).toHaveTextContent(messages.nvke.diagnostics.no);
  expect(screen.getByRole("note")).toHaveTextContent(messages.nvke.historicalHelp);
});
it("shows historical SPIRE agent reports without turning missing values into false", () => {
  const refresh = vi.fn();
  render(
    <NvkeDiagnosticsPanel
      observation={DIAGNOSTIC_OBSERVATION}
      table={fakeController({
        rows: [{ ...DIAGNOSTIC_ROW, reachable: null, hasValidSvid: false }],
      })}
      loading={false}
      error={null}
      onRefresh={refresh}
    />,
    { wrapper }
  );
  const row = screen.getByRole("row", { name: /gpu-node-a/ });
  expect(within(row).getByText(messages.nvke.diagnostics.notReported)).toBeInTheDocument();
  expect(within(row).getByText(messages.nvke.diagnostics.no)).toBeInTheDocument();
  expect(within(row).getByText("2026-11-07T12:00:00Z")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.refresh }));
  expect(refresh).toHaveBeenCalledOnce();
});
