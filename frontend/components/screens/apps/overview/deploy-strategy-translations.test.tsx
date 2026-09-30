import { MockedProvider } from "@apollo/client/testing/react";
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GET_APP } from "@/graphql/registry/registry.queries";
import { UPDATE_APP } from "@/graphql/registry/registry.mutations";
import { PermissionsProvider } from "@/providers/PermissionsProvider";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";

import { APPS } from "../list/fixtures";
import { DEPLOY_STRATEGY } from "./app-overview-cards-b.fixtures";
import { DeployStrategyCardView } from "./DeployStrategyCard";
import { useDeployStrategy } from "./use-deploy-strategy";

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

describe("translated deploy strategy", () => {
  it.each([
    ["fr", "Branche de déploiement", "Enregistrer la stratégie"],
    ["ja", "デプロイブランチ", "戦略を保存"],
  ] as const)(
    "keeps %s edits through rejection and sends the same branch on retry",
    async (locale, branch, save) => {
      const onError = vi.fn();
      const onSave = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
      render(
        <MockedProvider>
          <PermissionsProvider value={{ granted: new Set(["app.update"]), loading: false }}>
            <NextIntlClientProvider locale={locale} messages={catalogs[locale]} onError={onError}>
              <DeployStrategyCardView {...DEPLOY_STRATEGY} defaultOpen onSave={onSave} />
            </NextIntlClientProvider>
          </PermissionsProvider>
        </MockedProvider>
      );
      const dialog = screen.getByRole("dialog");
      fireEvent.change(within(dialog).getByLabelText(new RegExp(`^${branch}`)), {
        target: { value: "release/mobile" },
      });
      fireEvent.click(within(dialog).getByRole("button", { name: save }));
      await waitFor(() =>
        expect(onSave).toHaveBeenCalledWith({
          triggerMode: DEPLOY_STRATEGY.app.triggerMode,
          previewEnabled: DEPLOY_STRATEGY.app.previewEnabled,
          deployBranch: "release/mobile",
          cronExpression: "",
        })
      );
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      expect(within(dialog).getByLabelText(new RegExp(`^${branch}`))).toHaveValue("release/mobile");
      fireEvent.click(within(dialog).getByRole("button", { name: save }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(onSave).toHaveBeenCalledTimes(2);
      expect(onError).not.toHaveBeenCalled();
    }
  );

  it("uses the shared Korean version-conflict fallback for an actual typed mutation envelope", async () => {
    const onError = vi.fn();
    const app = APPS[0];
    const input = {
      id: app.id,
      triggerMode: "cron",
      deployBranch: "release/mobile",
      previewEnabled: true,
      cronExpression: "0 6 * * *",
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
    const { result } = renderHook(() => useDeployStrategy(app), { wrapper: Wrapper });
    await act(async () => {
      expect(
        await result.current.onSave({
          triggerMode: "cron",
          deployBranch: " release/mobile ",
          previewEnabled: true,
          cronExpression: " 0 6 * * * ",
        })
      ).toBe(false);
    });
    expect(notices.error).toHaveBeenCalledExactlyOnceWith(ko.shared.versionMismatch.changed);
    expect(notices.success).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  it.each(Object.entries(catalogs))(
    "renders all %s strategy messages and the rich branch help",
    (locale, messages) => {
      const onError = vi.fn();
      const t = createTranslator({ locale, messages: messages.apps.deployStrategy, onError });
      for (const key of leaves(en.apps.deployStrategy))
        expect(
          t.rich(key as Parameters<typeof t.rich>[0], { branch: () => "release/mobile" })
        ).toBeTruthy();
      expect(t.rich("deployBranchHelp", { branch: () => "release/mobile" })).toContain(
        "release/mobile"
      );
      expect(onError).not.toHaveBeenCalled();
    }
  );
});
