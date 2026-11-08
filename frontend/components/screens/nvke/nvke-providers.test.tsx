import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";
import { fakeController } from "@/components/data-table/fixtures";
import messages from "@/messages/en.json";
import { NvkeProvidersPanel } from "./NvkeProvidersPanel";
import { PROVIDER_PROPS } from "./NvkeProvidersPanel.stories";
const wrapper = ({ children }: { children: ReactNode }) => (
  <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
    {children}
  </NextIntlClientProvider>
);
it("keeps credential health independent of manager availability and preserves exact observations", () => {
  const refresh = vi.fn();
  render(<NvkeProvidersPanel {...PROVIDER_PROPS} onRefresh={refresh} />, { wrapper });
  expect(screen.getByText(messages.nvke.providers.states.DEGRADED)).toBeVisible();
  expect(screen.getByText(messages.nvke.providers.health.HEALTHY)).toBeVisible();
  expect(screen.getAllByText("9007199254740993")).toHaveLength(2);
  expect(screen.getByText("2026-11-08T11:59:00.123456789Z")).toBeVisible();
  expect(screen.getByText(messages.nvke.providers.optional)).toBeVisible();
  expect(screen.getByText(messages.nvke.providers.healthHelp)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: messages.nvke.refresh }));
  expect(refresh).toHaveBeenCalledOnce();
});
it.each([{ loading: true }, { error: "Observation unavailable" }])(
  "hides the entire retained snapshot while loading or failed %j",
  (change) => {
    render(<NvkeProvidersPanel {...PROVIDER_PROPS} {...change} />, { wrapper });
    expect(screen.queryByText("nico-credentials")).not.toBeInTheDocument();
    expect(screen.queryByText("9007199254740993")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  }
);
it("delegates identity paging and search to its scoped controller", async () => {
  const next = vi.fn(),
    search = vi.fn();
  render(
    <NvkeProvidersPanel
      {...PROVIDER_PROPS}
      identities={fakeController({ ...PROVIDER_PROPS.identities, next, setSearch: search })}
    />,
    { wrapper }
  );
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "additional-030" } });
  await waitFor(() => expect(search).toHaveBeenCalledWith("additional-030"));
  fireEvent.click(screen.getByRole("button", { name: messages.shared.pagination.older }));
  expect(next).toHaveBeenCalledOnce();
});
