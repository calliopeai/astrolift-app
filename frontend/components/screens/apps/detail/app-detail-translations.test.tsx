import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { userEvent } from "storybook/test";
import { describe, expect, it, vi } from "vitest";

import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";

import { FRAME } from "./app-frame.fixtures";
import { AppFrame } from "./AppFrame";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
function leaves(value: Record<string, unknown>, prefix = ""): string[] {
  return Object.entries(value).flatMap(([key, entry]) =>
    typeof entry === "string"
      ? [prefix + key]
      : leaves(entry as Record<string, unknown>, prefix + key + ".")
  );
}

describe("app detail translated confirmations", () => {
  it("keeps the actual image/environment in a French deploy confirmation across failure and retry", async () => {
    const onError = vi.fn();
    const onDeploy = vi
      .fn()
      .mockRejectedValueOnce(new Error("DENIED"))
      .mockResolvedValueOnce(undefined);
    render(
      <NextIntlClientProvider locale="fr" messages={fr} onError={onError}>
        <AppFrame {...FRAME} onDeploy={onDeploy}>
          body
        </AppFrame>
      </NextIntlClientProvider>
    );
    fireEvent.click(screen.getByRole("button", { name: "Déployer" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByRole("heading")).toHaveTextContent("sha-1112015d");
    expect(within(dialog).getByRole("heading")).toHaveTextContent("production");
    expect(onDeploy).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Déployer" }));
    await waitFor(() => expect(onDeploy).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    await waitFor(() =>
      expect(within(dialog).getByRole("button", { name: "Déployer" })).toBeEnabled()
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Déployer" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(onDeploy).toHaveBeenCalledTimes(2);
    expect(onError).not.toHaveBeenCalled();
  });

  it("does not delete until the translated Spanish confirmation is accepted", async () => {
    const onDelete = vi.fn().mockResolvedValue(undefined);
    render(
      <NextIntlClientProvider locale="es" messages={es}>
        <AppFrame {...FRAME} onDelete={onDelete}>
          body
        </AppFrame>
      </NextIntlClientProvider>
    );
    await userEvent.click(screen.getByRole("button", { name: "Más acciones" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Eliminar aplicación" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByRole("heading")).toHaveTextContent("¿Eliminar checkout?");
    expect(onDelete).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Eliminar aplicación" }));
    await waitFor(() => expect(onDelete).toHaveBeenCalledTimes(1));
  });

  it.each(Object.entries(catalogs))(
    "renders every %s detail message preserving IDs, reasons and measurements",
    (locale, messages) => {
      const onError = vi.fn();
      const t = createTranslator({ locale, messages: messages.apps.detail, onError });
      for (const key of leaves(en.apps.detail))
        expect(
          t(key as Parameters<typeof t>[0], {
            tag: "sha-123",
            slug: "checkout",
            env: "production",
            reason: "DNS_TIMEOUT",
            status: "failed",
            when: "08:00",
            field: "manifest_hash",
            latency: 12,
            statusCode: 503,
            ok: 2,
            total: 3,
            avgMs: 15,
            seconds: 30,
            minutes: 7,
            hours: 2,
            message: "DENIED",
          })
        ).toBeTruthy();
      expect(t("headerDeploy.title", { tag: "sha-123", env: "production" })).toContain("sha-123");
      expect(t("urlHealth.down", { reason: "DNS_TIMEOUT" })).toBe("DNS_TIMEOUT");
      expect(onError).not.toHaveBeenCalled();
    }
  );
});
