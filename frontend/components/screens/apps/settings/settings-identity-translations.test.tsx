import { MockedProvider } from "@apollo/client/testing/react";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { UPDATE_APP } from "@/graphql/registry/registry.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";

import { APPS } from "../list/fixtures";
import { IDENTITY } from "./app-settings-members.fixtures";
import { AppIdentityView } from "./AppIdentity";
import { useAppIdentity } from "./use-app-identity";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
const notices = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock("sonner", () => ({ toast: notices }));
beforeEach(() => {
  notices.error.mockClear();
  notices.success.mockClear();
});
function leaves(value: Record<string, unknown>, prefix = ""): string[] {
  return Object.entries(value).flatMap(([key, entry]) =>
    typeof entry === "string"
      ? [prefix + key]
      : leaves(entry as Record<string, unknown>, prefix + key + ".")
  );
}

describe("translated app identity and settings groups", () => {
  it.each([
    ["fr", "Nom", "Description", "Enregistrer"],
    ["ja", "名前", "説明", "保存"],
  ] as const)(
    "retains %s identity edits and the repository target across failed-save retry",
    async (locale, name, description, save) => {
      const onError = vi.fn();
      const onSave = vi
        .fn()
        .mockRejectedValueOnce(new Error("OWNER_SCOPE_DENIED"))
        .mockResolvedValueOnce(undefined);
      render(
        <NextIntlClientProvider locale={locale} messages={catalogs[locale]} onError={onError}>
          <AppIdentityView {...IDENTITY} onSave={onSave} />
        </NextIntlClientProvider>
      );
      expect(screen.getByRole("link", { name: IDENTITY.sourceRepo })).toHaveAttribute(
        "href",
        IDENTITY.sourceUrl
      );
      fireEvent.change(screen.getByLabelText(name), { target: { value: "Mobile Checkout" } });
      fireEvent.change(screen.getByLabelText(description), {
        target: { value: "User supplied text" },
      });
      fireEvent.click(screen.getByRole("button", { name: save }));
      await waitFor(() =>
        expect(onSave).toHaveBeenCalledExactlyOnceWith({
          name: "Mobile Checkout",
          description: "User supplied text",
        })
      );
      expect(await screen.findByRole("alert")).toHaveTextContent("OWNER_SCOPE_DENIED");
      expect(screen.getByLabelText(name)).toHaveValue("Mobile Checkout");
      expect(screen.getByLabelText(description)).toHaveValue("User supplied text");
      fireEvent.click(screen.getByRole("button", { name: save }));
      await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
      expect(onSave).toHaveBeenCalledTimes(2);
      expect(onError).not.toHaveBeenCalled();
    }
  );

  it("refuses a blank Spanish name with the translated validation message", () => {
    const onSave = vi.fn();
    const onError = vi.fn();
    render(
      <NextIntlClientProvider locale="es" messages={es} onError={onError}>
        <AppIdentityView {...IDENTITY} onSave={onSave} />
      </NextIntlClientProvider>
    );
    fireEvent.change(screen.getByLabelText("Nombre"), { target: { value: "   " } });
    expect(screen.getByRole("alert")).toHaveTextContent("La aplicación necesita un nombre.");
    expect(screen.getByLabelText("Nombre")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("button", { name: "Guardar" })).toBeDisabled();
    expect(onSave).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  it("localizes Korean typed version-conflict feedback and refuses a save to the exact app/version", async () => {
    const onError = vi.fn();
    const app = APPS[0];
    const input = {
      id: app.id,
      name: "Mobile Checkout",
      description: "User supplied text",
      ifMatchVersion: app.version,
    };
    function Wrapper({ children }: PropsWithChildren) {
      return (
        <MockedProvider
          mocks={[
            {
              request: { query: GET_APP, variables: { slug: app.slug, includeDrift: false } },
              result: { data: { astroliftApp: null } },
            },
            {
              request: { query: UPDATE_APP, variables: { input } },
              result: {
                data: {
                  updateApp: {
                    ok: false,
                    errors: [
                      {
                        code: "VERSION_MISMATCH",
                        message: null,
                        field: null,
                        currentVersion: 4,
                        requestedVersion: app.version,
                      },
                    ],
                    data: null,
                  },
                },
              },
            },
          ]}
        >
          <NextIntlClientProvider locale="ko" messages={ko} onError={onError}>
            {children}
          </NextIntlClientProvider>
        </MockedProvider>
      );
    }
    const { result } = renderHook(() => useAppIdentity(app), { wrapper: Wrapper });
    await act(async () => {
      await expect(
        result.current.onSave({ name: " Mobile Checkout ", description: " User supplied text " })
      ).rejects.toThrow(ko.apps.settingsTab.identity.toastFailed);
    });
    expect(notices.error).toHaveBeenCalledExactlyOnceWith(ko.shared.versionMismatch.changed);
    expect(notices.success).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  it.each(Object.entries(catalogs))(
    "renders all %s identity and group messages including the previously missing catalog",
    (locale, messages) => {
      const onError = vi.fn();
      const t = createTranslator({ locale, messages: messages.apps.settingsTab, onError });
      for (const key of leaves(en.apps.settingsTab))
        expect(t(key as Parameters<typeof t>[0])).toBeTruthy();
      expect(onError).not.toHaveBeenCalled();
    }
  );
});
