import { readFileSync } from "node:fs";
import path from "node:path";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { locales } from "@/i18n/config";
import { ConfirmProvider, useConfirm } from "@/hooks/use-confirm";
import { ConfirmDialog } from "./ConfirmDialog";

const toastError = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: { error: toastError } }));
beforeEach(() => toastError.mockClear());

function catalogue(locale: string) {
  return JSON.parse(readFileSync(path.resolve("messages", `${locale}.json`), "utf8"));
}

function Imperative({ complete }: { complete: (accepted: boolean) => void }) {
  const confirm = useConfirm();
  return (
    <button onClick={async () => complete(await confirm({ title: "fixture action" }))}>
      Launch
    </button>
  );
}

describe("translated shared confirmations", () => {
  it.each(locales)(
    "%s preserves validation, pending state, retry and trimmed reason",
    async (locale) => {
      const messages = catalogue(locale);
      const copy = messages.shared.confirmation;
      const onError = vi.fn();
      const close = vi.fn();
      let resolve!: () => void;
      let reject!: (error: unknown) => void;
      const action = vi.fn(
        () =>
          new Promise<void>((yes, no) => {
            resolve = yes;
            reject = no;
          })
      );
      const { rerender } = render(
        <NextIntlClientProvider locale={locale} messages={messages} onError={onError}>
          <ConfirmDialog
            open
            onOpenChange={close}
            title="fixture action"
            reason={{ label: "fixture reason" }}
            onConfirm={action}
          />
        </NextIntlClientProvider>
      );
      fireEvent.click(screen.getByRole("button", { name: copy.confirm }));
      expect(screen.getByRole("alert")).toHaveTextContent(copy.reasonRequired);
      expect(action).not.toHaveBeenCalled();
      fireEvent.change(screen.getByRole("textbox", { name: "fixture reason" }), {
        target: { value: "  actual reason  " },
      });
      fireEvent.click(screen.getByRole("button", { name: copy.confirm }));
      expect(action).toHaveBeenLastCalledWith("actual reason");
      expect(screen.getByRole("button", { name: copy.working })).toBeDisabled();
      expect(screen.getByRole("button", { name: copy.cancel })).toBeDisabled();
      expect(screen.getByRole("textbox")).toBeDisabled();
      await act(async () => reject({}));
      expect(toastError).toHaveBeenLastCalledWith(copy.failed);
      expect(close).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: copy.confirm })).toBeEnabled();
      fireEvent.click(screen.getByRole("button", { name: copy.confirm }));
      await act(async () => reject(new Error("actual server diagnostic")));
      expect(toastError).toHaveBeenLastCalledWith("actual server diagnostic");
      fireEvent.click(screen.getByRole("button", { name: copy.confirm }));
      await act(async () => resolve());
      expect(action).toHaveBeenCalledTimes(3);
      expect(close).toHaveBeenCalledExactlyOnceWith(false);
      rerender(
        <NextIntlClientProvider locale={locale} messages={messages} onError={onError}>
          <ConfirmDialog
            open
            onOpenChange={close}
            title="fixture action"
            confirmLabel="custom confirm"
            cancelLabel="custom cancel"
            confirmDisabled
            onConfirm={action}
          />
        </NextIntlClientProvider>
      );
      expect(screen.getByRole("button", { name: "custom confirm" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "custom cancel" })).toBeEnabled();
      expect(onError).not.toHaveBeenCalled();
    }
  );

  it.each(locales)("%s resolves imperative cancellation and confirmation", async (locale) => {
    const messages = catalogue(locale);
    const complete = vi.fn();
    const onError = vi.fn();
    render(
      <NextIntlClientProvider locale={locale} messages={messages} onError={onError}>
        <ConfirmProvider>
          <Imperative complete={complete} />
        </ConfirmProvider>
      </NextIntlClientProvider>
    );
    fireEvent.click(screen.getByRole("button", { name: "Launch" }));
    fireEvent.click(screen.getByRole("button", { name: messages.shared.confirmation.cancel }));
    await waitFor(() => expect(complete).toHaveBeenLastCalledWith(false));
    fireEvent.click(screen.getByRole("button", { name: "Launch" }));
    fireEvent.click(screen.getByRole("button", { name: messages.shared.confirmation.confirm }));
    await waitFor(() => expect(complete).toHaveBeenLastCalledWith(true));
    expect(complete).toHaveBeenCalledTimes(2);
    expect(onError).not.toHaveBeenCalled();
  });
});
