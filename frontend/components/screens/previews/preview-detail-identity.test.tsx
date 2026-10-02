import { readFileSync } from "node:fs";
import { fireEvent, render, screen } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { expect, it, vi } from "vitest";
import { PreviewDetailScreen } from "./PreviewDetail";
import { previewDetailProps } from "./previews.fixtures";

it.each(["en", "es", "fr", "de", "pt-BR", "ja", "ko", "zh-Hans"])(
  "%s renders exact binding and explicit runtime/log/history controls",
  (locale) => {
    const messages = JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"));
    const t = createTranslator({ locale, messages, namespace: "lists.previews" });
    const props = previewDetailProps();
    props.preview = { ...props.preview!, runtimeStatus: "not_requested" };
    const onLoadRuntime = vi.fn();
    const onLoadLogs = vi.fn();
    const onLoadDeployments = vi.fn();
    const view = render(
      <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
        <PreviewDetailScreen
          {...props}
          onLoadRuntime={onLoadRuntime}
          onLoadLogs={onLoadLogs}
          onLoadDeployments={onLoadDeployments}
        />
      </NextIntlClientProvider>
    );
    expect(
      screen.getByText(
        `${props.preview!.environment!.environmentName} · ${t("detail.binding.available")}`
      )
    ).toBeVisible();
    expect(screen.getByText(t("detail.resourcesNotRequested"))).toBeVisible();
    for (const [label, action] of [
      ["loadResources", onLoadRuntime],
      ["loadLogs", onLoadLogs],
      ["loadDeployments", onLoadDeployments],
    ] as const) {
      fireEvent.click(screen.getByRole("button", { name: t(`detail.${label}`) }));
      expect(action).toHaveBeenCalledOnce();
    }
    expect(screen.queryByText("$0.00")).toBeNull();
    view.unmount();
  }
);

it.each(["retired", "unavailable"])(
  "%s retains metadata but exposes no hostname or fallback log action",
  (environmentStatus) => {
    const messages = JSON.parse(readFileSync("messages/en.json", "utf8"));
    const t = createTranslator({ locale: "en", messages, namespace: "lists.previews" });
    const props = previewDetailProps();
    const preview = {
      ...props.preview!,
      isManual: true,
      prNumber: 0,
      branch: "manual-branch",
      environmentStatus,
      hostname: "retained.example.invalid",
    };
    const view = render(
      <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
        <PreviewDetailScreen {...props} preview={preview} />
      </NextIntlClientProvider>
    );
    expect(screen.getByRole("heading", { name: "manual-branch" })).toBeVisible();
    expect(
      screen.getByText(
        `${preview.environment!.environmentName} · ${t(`detail.binding.${environmentStatus}`)}`
      )
    ).toBeVisible();
    expect(view.container.querySelector('a[href="https://retained.example.invalid"]')).toBeNull();
    expect(view.container.querySelector('a[href$="/logs"]')).toBeNull();
    for (const label of ["loadResources", "loadLogs", "loadDeployments"])
      expect(screen.getByRole("button", { name: t(`detail.${label}`) })).toBeDisabled();
    view.unmount();
  }
);
