import { readFileSync } from "node:fs";
import path from "node:path";
import { act, renderHook } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { locales } from "@/i18n/config";

import { useVersionMismatch } from "./use-version-mismatch";

const error = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: { error } }));
beforeEach(() => error.mockClear());

describe("localized optimistic concurrency feedback", () => {
  it.each(locales)("%s uses its fallback and preserves refresh behavior", async (locale) => {
    const messages = JSON.parse(readFileSync(path.resolve("messages", `${locale}.json`), "utf8"));
    const onError = vi.fn();
    const onRefresh = vi.fn().mockResolvedValue(undefined);
    function Wrapper({ children }: PropsWithChildren) {
      return (
        <NextIntlClientProvider locale={locale} messages={messages} onError={onError}>
          {children}
        </NextIntlClientProvider>
      );
    }
    const { result, unmount } = renderHook(useVersionMismatch, { wrapper: Wrapper });
    expect(result.current({ ok: false, errors: [{ code: "DENIED" }] }, { onRefresh })).toBe(false);
    expect(result.current({ ok: true }, { onRefresh })).toBe(false);
    expect(error).not.toHaveBeenCalled();
    expect(
      result.current(
        { ok: false, errors: [{ code: "VERSION_MISMATCH", currentVersion: 3 }] },
        { onRefresh }
      )
    ).toBe(true);
    expect(error).toHaveBeenLastCalledWith(messages.shared.versionMismatch.changed, {
      action: { label: messages.shared.versionMismatch.refresh, onClick: expect.any(Function) },
    });
    expect(onRefresh).not.toHaveBeenCalled();
    await act(async () => {
      error.mock.calls.at(-1)?.[1].action.onClick();
    });
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(
      result.current({
        ok: false,
        errors: [{ code: "VERSION_MISMATCH", message: "actual server diagnostic" }],
      })
    ).toBe(true);
    expect(error).toHaveBeenLastCalledWith("actual server diagnostic");
    expect(onError).not.toHaveBeenCalled();
    unmount();
  });
});
