import { MockedProvider } from "@apollo/client/testing/react";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { UPDATE_APP } from "@/graphql/registry/registry.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";
import de from "@/messages/de.json";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import pt from "@/messages/pt-BR.json";
import zh from "@/messages/zh-Hans.json";

import { BUILD, BUILD_ROOT } from "./app-settings-members.fixtures";
import { BuildSettingsView } from "./BuildSettings";
import { type BuildSettingsSource, useBuildSettings } from "./use-build-settings";

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

describe("build settings (astrolift-cli #159)", () => {
  it.each([
    ["fr", "Contexte de compilation", "Enregistrer", /astro ci setup checkout/],
    ["ja", "ビルドコンテキスト", "保存", /astro ci setup checkout/],
  ] as const)(
    "keeps %s edits across a failed save, then says the CI workflow needs a sync",
    async (locale, contextLabel, save, notice) => {
      const onError = vi.fn();
      const onSave = vi
        .fn()
        .mockRejectedValueOnce(new Error("VALIDATION: buildContext"))
        .mockResolvedValueOnce(undefined);
      render(
        <NextIntlClientProvider locale={locale} messages={catalogs[locale]} onError={onError}>
          <BuildSettingsView {...BUILD_ROOT} onSave={onSave} />
        </NextIntlClientProvider>
      );
      fireEvent.change(screen.getByLabelText(contextLabel), {
        target: { value: "src/checkout" },
      });
      fireEvent.change(screen.getByLabelText("Dockerfile"), {
        target: { value: "Dockerfile.prod" },
      });
      fireEvent.click(screen.getByRole("button", { name: save }));
      await waitFor(() =>
        expect(onSave).toHaveBeenCalledExactlyOnceWith({
          buildContext: "src/checkout",
          dockerfilePath: "Dockerfile.prod",
        })
      );
      expect(await screen.findByRole("alert")).toHaveTextContent("VALIDATION: buildContext");
      expect(screen.getByLabelText(contextLabel)).toHaveValue("src/checkout");
      expect(screen.queryByRole("status")).not.toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: save }));
      expect(await screen.findByRole("status")).toHaveTextContent(notice);
      expect(onSave).toHaveBeenCalledTimes(2);
      expect(onError).not.toHaveBeenCalled();
    }
  );

  it("refuses a blank Spanish build context with the translated message", () => {
    const onSave = vi.fn();
    const onError = vi.fn();
    render(
      <NextIntlClientProvider locale="es" messages={es} onError={onError}>
        <BuildSettingsView {...BUILD} onSave={onSave} />
      </NextIntlClientProvider>
    );
    fireEvent.change(screen.getByLabelText("Contexto de compilación"), { target: { value: " " } });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Escribe un contexto de compilación. Usa . para la raíz del repositorio."
    );
    expect(screen.getByRole("button", { name: "Guardar" })).toBeDisabled();
    expect(onSave).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  it("says nothing about CI after a save on an app without a repository", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <NextIntlClientProvider locale="de" messages={de}>
        <BuildSettingsView {...BUILD} hasRepository={false} onSave={onSave} />
      </NextIntlClientProvider>
    );
    fireEvent.change(screen.getByLabelText("Build-Kontext"), { target: { value: "src" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("saves trimmed paths to the exact app and version, and localizes a Korean version conflict", async () => {
    const onError = vi.fn();
    const app: BuildSettingsSource = {
      id: "a0000000-0000-4000-8000-000000000001",
      slug: "abbott-cgm",
      version: 7,
      buildContext: ".",
      dockerfilePath: "Dockerfile",
      buildMode: "ci_pushed",
      manifestPath: "src/abbott-cgm/astrolift.toml",
      sourceRepo: "steady/monorepo",
    };
    const input = {
      id: app.id,
      buildContext: "src/abbott-cgm",
      dockerfilePath: "Dockerfile",
      ifMatchVersion: 7,
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
                        currentVersion: 8,
                        requestedVersion: 7,
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
    const { result } = renderHook(() => useBuildSettings(app), { wrapper: Wrapper });
    await act(async () => {
      await expect(
        result.current.onSave({ buildContext: " src/abbott-cgm ", dockerfilePath: " Dockerfile " })
      ).rejects.toThrow(ko.apps.settingsTab.build.toastFailed);
    });
    expect(notices.error).toHaveBeenCalledExactlyOnceWith(ko.shared.versionMismatch.changed);
    expect(notices.success).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  it.each(Object.entries(catalogs))("renders every %s build message", (locale, messages) => {
    const onError = vi.fn();
    const t = createTranslator({ locale, messages: messages.apps.settingsTab.build, onError });
    for (const key of leaves(en.apps.settingsTab.build))
      expect(t(key as Parameters<typeof t>[0])).toBeTruthy();
    expect(onError).not.toHaveBeenCalled();
  });
});
