import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import * as React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import messages from "@/messages/en.json";
import { useLocalListState } from "@/components/list/use-list-state";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppsListScreen } from "./apps/list/AppsListScreen";
import { APPS_LIST } from "./apps/list/apps-list";
import { APPS, listProps } from "./apps/list/fixtures";
import { MessageLogPanelView } from "./apps/managed-services/EmailDetailSheet";
import { MESSAGE_LOG } from "./apps/managed-services/app-managed-services.fixtures";
import {
  SourceHostsView,
  WebhookSecretReveal,
} from "./settings/source-providers/SourceProvidersScreen";
import { HOSTS, REVEAL } from "./settings/source-providers/settings-source-providers.fixtures";
import { SOURCE_HOSTS_LIST } from "./settings/source-providers/source-providers-list";

const mocks = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock("sonner", () => ({ toast: mocks }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/apps",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true, loading: false }),
}));
function wrapper({ children }: React.PropsWithChildren) {
  return (
    <NextIntlClientProvider locale="en" messages={messages}>
      <TooltipProvider>{children}</TooltipProvider>
    </NextIntlClientProvider>
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

function Reveal() {
  const [open, setOpen] = React.useState(false);
  return (
    <>
      <button onClick={() => setOpen(true)}>Show rotated secret</button>
      {open && <WebhookSecretReveal reveal={REVEAL} onClose={() => setOpen(false)} />}
    </>
  );
}

describe("app card navigation", () => {
  it("offers separate keyboard stops for the app, its pin action and failed deployment", async () => {
    localStorage.setItem(`astrolift.list.${APPS_LIST.id}.mode`, JSON.stringify("card"));
    const togglePin = vi.fn();
    function Apps() {
      const list = useLocalListState(APPS_LIST);
      return (
        <AppsListScreen
          {...listProps({ rows: [APPS[1]], totalCount: 1, pinned: new Set(), togglePin })}
          list={list}
        />
      );
    }
    render(<Apps />, { wrapper });
    const app = await screen.findByRole("link", { name: "Billing Worker" });
    const failed = screen.getByRole("link", { name: "View failed deploy" });
    expect(app).toHaveAttribute("href", "/apps/billing-worker");
    expect(failed).toHaveAttribute("href", `/deployments/${APPS[1].latestDeployment!.id}`);
    expect(app.querySelector("a,button")).toBeNull();
    expect(failed.closest("a a")).toBeNull();
    app.focus();
    const user = userEvent.setup();
    await user.tab();
    expect(screen.getByRole("button", { name: "Pin to top" })).toHaveFocus();
    await user.keyboard(" ");
    expect(togglePin).toHaveBeenCalledExactlyOnceWith("billing-worker");
    await user.tab();
    expect(failed).toHaveFocus();
  });
  it("retains ordinary app navigation in the table mode", () => {
    function Apps() {
      const list = useLocalListState(APPS_LIST);
      return <AppsListScreen {...listProps({ rows: [APPS[1]], totalCount: 1 })} list={list} />;
    }
    render(<Apps />, { wrapper });
    expect(screen.getByRole("link", { name: /Billing Worker/ })).toHaveAttribute(
      "href",
      "/apps/billing-worker"
    );
  });
});

describe("email message keyboard drill-in", () => {
  it("opens bounce and complaint details with Enter and Space, then closes the focused disclosure", async () => {
    render(<MessageLogPanelView {...MESSAGE_LOG} />, { wrapper });
    const user = userEvent.setup();
    const bounce = screen.getByRole("button", { name: "bounced@customer.example.com" });
    bounce.focus();
    await user.keyboard("{Enter}");
    const details = screen.getByRole("region", {
      name: "Message details for bounced@customer.example.com",
    });
    expect(within(details).getByText("Permanent")).toBeVisible();
    expect(bounce).toHaveAttribute("aria-controls", details.id);
    expect(bounce).toHaveAttribute("aria-expanded", "true");
    const complaint = screen.getByRole("button", { name: "angry@customer.example.com" });
    complaint.focus();
    await user.keyboard(" ");
    expect(
      within(
        screen.getByRole("region", { name: "Message details for angry@customer.example.com" })
      ).getByText("abuse")
    ).toBeVisible();
    expect(screen.getAllByRole("button", { name: "Copy message ID" })).toHaveLength(2);
    bounce.focus();
    await user.keyboard("{Enter}");
    expect(bounce).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByRole("region", { name: "Message details for bounced@customer.example.com" })
    ).not.toBeInTheDocument();
    expect(bounce).toHaveFocus();
    expect(
      screen.queryByRole("button", { name: "ada@customer.example.com" })
    ).not.toBeInTheDocument();
  });
});

describe("one-time webhook secret dialog", () => {
  it("announces the title and description, traps keyboard focus and restores the opener after Escape", async () => {
    render(<Reveal />, { wrapper });
    const user = userEvent.setup();
    const opener = screen.getByRole("button", { name: "Show rotated secret" });
    opener.focus();
    await user.keyboard("{Enter}");
    const dialog = screen.getByRole("dialog", { name: "Webhook secret generated" });
    expect(dialog).toHaveAccessibleDescription(/plaintext secret is shown exactly once/);
    expect(dialog).toHaveAttribute("aria-modal", "true");
    const copy = within(dialog).getByRole("button", { name: "Copy URL" });
    await waitFor(() => expect(copy).toHaveFocus());
    await user.tab({ shift: true });
    expect(within(dialog).getByRole("button", { name: "Close" })).toHaveFocus();
    await user.tab();
    expect(copy).toHaveFocus();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(opener).toHaveFocus());
  });
  it("preserves the one-time value after a clipboard failure and closes with Done", async () => {
    render(<Reveal />, { wrapper });
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockRejectedValue(new Error("Denied")) },
    });
    await user.click(screen.getByRole("button", { name: "Show rotated secret" }));
    await user.click(screen.getByRole("button", { name: "Copy secret" }));
    expect(mocks.error).toHaveBeenCalledWith("Couldn't copy — select the text and copy manually");
    expect(mocks.success).not.toHaveBeenCalled();
    expect(screen.getByText(REVEAL.plaintextSecret)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
  it("returns focus to the source region after the rotation confirmation has unmounted", async () => {
    function Hosts() {
      const list = useLocalListState(SOURCE_HOSTS_LIST);
      return (
        <SourceHostsView
          {...HOSTS}
          rows={[HOSTS.rows[0]]}
          incompleteClientIdConnections={[]}
          list={list}
          renderConnectGithub={() => null}
          renderConnectGitlab={() => null}
          renderConnectSource={() => null}
          renderAddClientId={() => null}
        />
      );
    }
    render(<Hosts />, { wrapper });
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Connections: row actions" }));
    await user.click(screen.getByRole("menuitem", { name: "Webhook secret" }));
    await user.click(screen.getByRole("button", { name: "Rotate secret" }));
    await screen.findByRole("dialog", { name: "Webhook secret generated" });
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getByRole("region", { name: "Source connections" })).toHaveFocus()
    );
  });
});
