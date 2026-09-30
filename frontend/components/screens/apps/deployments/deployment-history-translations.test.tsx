import { fireEvent, render, screen, within } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider, useTranslations } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import { effectiveFilters } from "@/components/list/list-state";
import { useLocalListState } from "@/components/list/use-list-state";
import { TooltipProvider } from "@/components/ui/tooltip";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";
import {
  appDeploymentsList,
  localizedAppDeploymentsList,
  pageVariables,
} from "./app-deployments-list";
import {
  COMPARE,
  DEPLOY_RUNNING,
  DEPLOY_SUPERSEDED,
  SCREEN,
} from "./app-deployments-logs.fixtures";
import { AppDeploymentsScreen, type CompareSlotArgs } from "./AppDeploymentsScreen";
import { CompareDeploymentsSheetView } from "./CompareDeploymentsSheet";
const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
function leaves(value: Record<string, unknown>, prefix = ""): string[] {
  return Object.entries(value).flatMap(([key, entry]) =>
    typeof entry === "string"
      ? [prefix + key]
      : leaves(entry as Record<string, unknown>, prefix + key + ".")
  );
}
function History({ onCompare }: { onCompare: (args: CompareSlotArgs) => React.ReactNode }) {
  const t = useTranslations("apps.deployments");
  const list = useLocalListState(
    localizedAppDeploymentsList((key) => t(key as Parameters<typeof t>[0]), ["prod", "stg"])
  );
  return (
    <AppDeploymentsScreen
      {...SCREEN}
      rows={[DEPLOY_RUNNING, DEPLOY_SUPERSEDED]}
      list={list}
      renderCompare={onCompare}
    />
  );
}
describe("translated app deployment history", () => {
  it.each([
    ["fr", "Comparer", "Comparer (sélectionnez exactement 2)", "Déploiement"],
    ["ja", "比較", "比較（2 件を選択）", "デプロイ"],
  ] as const)(
    "requires two actual rows in %s and orders the comparison oldest first",
    (locale, compare, selectTwo, heading) => {
      const onError = vi.fn();
      const onCompare = vi.fn(() => null);
      render(
        <NextIntlClientProvider
          locale={locale}
          messages={catalogs[locale]}
          timeZone="UTC"
          now={new Date("2026-09-30T12:00:00Z")}
          onError={onError}
        >
          <TooltipProvider>
            <History onCompare={onCompare} />
          </TooltipProvider>
        </NextIntlClientProvider>
      );
      expect(screen.getByRole("columnheader", { name: heading })).toBeInTheDocument();
      const current = screen.getByRole("row", {
        name: (name) => name.includes(DEPLOY_RUNNING.imageTag),
      });
      const old = screen.getByRole("row", {
        name: (name) => name.includes(DEPLOY_SUPERSEDED.imageTag),
      });
      expect(within(current).getByRole("link")).toHaveAttribute(
        "href",
        `#deployment-${DEPLOY_RUNNING.id}`
      );
      fireEvent.click(within(current).getByRole("checkbox"));
      expect(screen.getByRole("button", { name: selectTwo })).toBeDisabled();
      expect(onCompare).not.toHaveBeenCalled();
      fireEvent.click(within(old).getByRole("checkbox"));
      fireEvent.click(screen.getByRole("button", { name: compare }));
      expect(onCompare).toHaveBeenCalledWith(
        expect.objectContaining({ open: true, deployA: DEPLOY_SUPERSEDED, deployB: DEPLOY_RUNNING })
      );
      expect(onError).not.toHaveBeenCalled();
    }
  );
  it("renders German diff copy without changing JSON paths, values or the source URL", () => {
    const onError = vi.fn();
    render(
      <NextIntlClientProvider locale="de" messages={de} onError={onError}>
        <CompareDeploymentsSheetView {...COMPARE} />
      </NextIntlClientProvider>
    );
    expect(screen.getByRole("link", { name: "Beim Code-Anbieter ansehen" })).toHaveAttribute(
      "href",
      COMPARE.comparison!.compareUrl
    );
    for (const text of ["(3 Änderungen)", "+ hinzufügen", "− entfernen", "~ ersetzen"])
      expect(screen.getByText(text)).toBeInTheDocument();
    for (const entry of COMPARE.comparison!.manifestDiff)
      expect(screen.getByText(entry.path)).toBeInTheDocument();
    expect(screen.getByText(/CHECKOUT_V2/)).toHaveTextContent('"true"');
    expect(screen.getByText(/port/)).toHaveTextContent("8080");
    expect(screen.getByText(/3 layers changed/).textContent).toBe(
      COMPARE.comparison!.imageDiffSummary
    );
    expect(onError).not.toHaveBeenCalled();
  });
  it.each(Object.entries(catalogs))(
    "%s resolves every deployment message and keeps view filters and requests unchanged",
    (locale, messages) => {
      const onError = vi.fn();
      const t = createTranslator({
        locale,
        messages: messages.apps.deployments,
        onError,
      });
      const values = {
        name: "checkout-api",
        action: "ACTION",
        status: "STATUS",
        env: "prod",
        tag: "sha-123",
        app: "checkout-api",
        slug: "checkout-api",
        filtered: 2,
        total: 8,
        received: 1,
        required: 2,
        count: 2,
      };
      for (const key of leaves(en.apps.deployments))
        expect(t(key as Parameters<typeof t>[0], values)).not.toBe(`apps.deployments.${key}`);
      const definition = localizedAppDeploymentsList(
        (key) => t(key as Parameters<typeof t>[0]),
        ["prod", "stg"]
      );
      const original = appDeploymentsList(["prod", "stg"]);
      const fields = (d: typeof definition) =>
        d.fields.map((field) => ({
          key: field.key,
          values: field.options?.map((option) => option.value),
        }));
      expect(fields(definition)).toEqual(fields(original));
      expect(definition.views.map(({ key, filters }) => ({ key, filters }))).toEqual(
        original.views.map(({ key, filters }) => ({ key, filters }))
      );
      for (const view of definition.views) {
        const state = {
          view: view.key,
          filters: { env: "stg" },
          q: " sha-123 ",
          sort: [{ key: "started", dir: "desc" as const }],
          page: 1,
          pageSize: 50,
          after: "next-cursor",
        };
        expect(
          pageVariables("checkout-api", effectiveFilters(definition, state), state, 1790769600000)
        ).toEqual(
          pageVariables("checkout-api", effectiveFilters(original, state), state, 1790769600000)
        );
      }
      expect(
        localizedAppDeploymentsList((key) => t(key as Parameters<typeof t>[0]), ["prod"], {
          previews: false,
        }).views.some(({ key }) => key === "previews")
      ).toBe(false);
      expect(t("compare.changes", { count: 1 })).not.toEqual(t("compare.changes", { count: 2 }));
      expect(onError).not.toHaveBeenCalled();
    }
  );
});
