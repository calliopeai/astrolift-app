import { act, fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";
import { ModelHostingSourcePanel, type ModelHostingSourceProps } from "./ModelHostingSourcePanel";
const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
function props(): ModelHostingSourceProps {
  return {
    scopeKey: "org-a:actor:allowed",
    allowed: true,
    authorityError: null,
    onRetryAuthority: vi.fn(),
    connections: [],
    selectedConnection: null,
    connectionsLoading: false,
    connectionsError: null,
    connectionPage: 1,
    connectionPages: 1,
    onConnectionPage: vi.fn(),
    onRetryConnections: vi.fn(),
    onSelectConnection: vi.fn(),
    onConnect: vi.fn(async () => ({
      accepted: true as const,
      account: "literal-hf-user",
      current: true,
      refreshFailed: false,
    })),
    onManualSource: vi.fn(),
  };
}
function view(p: ModelHostingSourceProps, locale: keyof typeof catalogs = "en") {
  return (
    <NextIntlClientProvider locale={locale} messages={catalogs[locale]} timeZone="UTC">
      <ModelHostingSourcePanel {...p} />
    </NextIntlClientProvider>
  );
}
function draft(locale: keyof typeof catalogs = "en") {
  const t = catalogs[locale].models.shared.hosting;
  fireEvent.click(screen.getByText(t.connect, { selector: "summary" }));
  fireEvent.change(screen.getByLabelText(t.connectionName), {
    target: { value: "literal connection" },
  });
  fireEvent.change(screen.getByLabelText(t.token), { target: { value: "hf_test_only_marker" } });
  fireEvent.click(screen.getByRole("button", { name: t.connect }));
}
describe("write-only model connection review", () => {
  it.each(Object.keys(catalogs) as (keyof typeof catalogs)[])(
    "%s saves without revealing stored credentials",
    async (locale) => {
      const p = props(),
        t = catalogs[locale].models.shared.hosting;
      render(view(p, locale));
      draft(locale);
      expect(
        await screen.findByText(t.connected.replace("{account}", "literal-hf-user"))
      ).toBeInTheDocument();
      expect(p.onConnect).toHaveBeenCalledExactlyOnceWith(
        "literal connection",
        "hf_test_only_marker"
      );
      expect(screen.getByLabelText(t.token)).toHaveValue("");
      expect(screen.getByLabelText(t.token)).toHaveAttribute("type", "password");
    }
  );
  it("retains refused drafts and literal diagnostic", async () => {
    const p = props();
    p.onConnect = vi.fn(async () => ({
      accepted: false as const,
      message: "Current hosting authority was removed",
    }));
    render(view(p));
    draft();
    expect(await screen.findByText("Current hosting authority was removed")).toBeInTheDocument();
    expect(screen.getByLabelText(en.models.shared.hosting.token)).toHaveValue(
      "hf_test_only_marker"
    );
  });
  it("keeps accepted connection distinct from failed inventory refresh", async () => {
    const p = props();
    p.onConnect = vi.fn(async () => ({
      accepted: true as const,
      account: null,
      current: true,
      refreshFailed: true,
    }));
    render(view(p));
    draft();
    expect(await screen.findByText(en.models.shared.hosting.connectionSaved)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      en.models.shared.hosting.savedRefreshFailed
    );
    expect(screen.getByLabelText(en.models.shared.hosting.token)).toHaveValue("");
  });
  it.each(["accepted", "rejected", "throw"])(
    "ignores unmounted %s reply after organization A→B→A",
    async (kind) => {
      let done!: (value: Awaited<ReturnType<ModelHostingSourceProps["onConnect"]>>) => void,
        fail!: (error: Error) => void;
      const p = props();
      p.onConnect = vi.fn(
        () =>
          new Promise<Awaited<ReturnType<ModelHostingSourceProps["onConnect"]>>>(
            (resolve, reject) => {
              done = resolve;
              fail = reject;
            }
          )
      );
      const { rerender } = render(view(p));
      draft();
      rerender(view({ ...p, scopeKey: "org-b:actor:allowed" }));
      rerender(view(p));
      fireEvent.change(screen.getByLabelText(en.models.shared.hosting.token), {
        target: { value: "new_draft" },
      });
      await act(async () => {
        if (kind === "throw") fail(new Error("late transport"));
        else
          done(
            kind === "accepted"
              ? { accepted: true, account: "old_account", current: false, refreshFailed: false }
              : { accepted: false, message: "old refusal" }
          );
      });
      expect(screen.getByLabelText(en.models.shared.hosting.token)).toHaveValue("new_draft");
      expect(screen.queryByText("old refusal")).not.toBeInTheDocument();
      expect(screen.queryByText("Connected as old_account.")).not.toBeInTheDocument();
      expect(screen.queryByText(en.models.shared.hosting.connectFailed)).not.toBeInTheDocument();
    }
  );
  it("does not turn failed initial inventory into a healthy empty picker", () => {
    const p = props();
    p.connectionsError = "Read unavailable";
    render(view(p));
    expect(screen.getByLabelText(en.models.shared.hosting.connection)).toBeDisabled();
    expect(screen.queryByText(en.models.shared.hosting.noConnections)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: en.models.shared.hosting.retry }));
    expect(p.onRetryConnections).toHaveBeenCalledOnce();
  });
});
