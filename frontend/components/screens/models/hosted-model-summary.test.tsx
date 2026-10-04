import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import pt from "@/messages/pt-BR.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import { ModelSubscriptionsPanel } from "./ModelSubscriptionsPanel";
import { ModelObservationsPanel } from "./ModelObservationsPanel";
import { subscriptionProps } from "./shared-model.fixtures";
import { modelObservationsProps } from "./model-observations.fixtures";
const locales = { en, es, fr, de, "pt-BR": pt, ja, ko, "zh-Hans": zh };
function view(children: React.ReactNode, locale: keyof typeof locales = "en") {
  return render(
    <NextIntlClientProvider locale={locale} messages={locales[locale]} timeZone="UTC">
      {children}
    </NextIntlClientProvider>
  );
}
describe("hosted model connection and usage summaries", () => {
  it("distinguishes authorized subscription count from apps on this page and preserves exact app links", () => {
    const row = subscriptionProps.subscriptions.rows[0];
    view(
      <ModelSubscriptionsPanel
        {...subscriptionProps}
        subscriptions={{
          ...subscriptionProps.subscriptions,
          totalCount: 12,
          rows: [row, { ...row, id: "second", environmentName: "other" }],
        }}
      />
    );
    expect(screen.getByText(/12 visible subscriptions/)).toBeInTheDocument();
    expect(screen.getByText(/1 apps on this page/)).toBeInTheDocument();
    const links = screen.getAllByRole("link", { name: row.appSlug });
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", `/apps/${encodeURIComponent(row.appSlug)}`);
    expect(screen.queryByText(/12 apps/)).not.toBeInTheDocument();
  });
  it.each(["stale", "error", "loading"] as const)(
    "does not advertise fresh connection counts or app links for a %s page",
    (state) => {
      view(
        <ModelSubscriptionsPanel
          {...subscriptionProps}
          subscriptions={{
            ...subscriptionProps.subscriptions,
            totalCount: 12,
            stale: state === "stale",
            loading: state === "loading",
            error: state === "error" ? new Error("Read refused") : null,
          }}
        />
      );
      expect(screen.queryByText(/12 visible subscriptions/)).not.toBeInTheDocument();
      expect(
        screen.queryByRole("link", { name: subscriptionProps.subscriptions.rows[0].appSlug })
      ).not.toBeInTheDocument();
    }
  );
  it("keeps serving configuration, per-app attribution and unmeasured cost truthful", () => {
    view(
      <ModelObservationsPanel
        {...modelObservationsProps}
        metrics={{
          ...modelObservationsProps.metrics,
          data: {
            ...modelObservationsProps.metrics.data!,
            metrics: modelObservationsProps.metrics.data!.metrics.map((metric) => ({
              ...metric,
              state: "UNCONFIGURED",
              value: null,
              samples: [],
            })),
          },
        }}
      />
    );
    expect(screen.getByText(en.models.shared.inventory.metricsUnconfigured)).toBeInTheDocument();
    expect(screen.getByText(en.models.shared.subscriptionUsage.selectionHint)).toBeInTheDocument();
    expect(screen.getByText(en.models.shared.inventory.costUnavailable)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: en.models.shared.inventory.metricsSetup })
    ).toHaveAttribute("href", "/documentation/cluster-prerequisites");
    expect(screen.queryByText(/metrics available/)).not.toBeInTheDocument();
  });
  it("reports current finite samples separately from stale/unsupported values", () => {
    view(
      <ModelObservationsPanel
        {...modelObservationsProps}
        metrics={{
          ...modelObservationsProps.metrics,
          data: {
            ...modelObservationsProps.metrics.data!,
            metrics: [
              modelObservationsProps.metrics.data!.metrics[0],
              { ...modelObservationsProps.metrics.data!.metrics[1], state: "STALE" },
              { ...modelObservationsProps.metrics.data!.metrics[2], state: "NO_DATA", value: null },
            ],
          },
        }}
      />
    );
    expect(screen.getByText("1 deployment metrics available")).toBeInTheDocument();
    expect(screen.queryByText("3 deployment metrics available")).not.toBeInTheDocument();
  });
  it.each(Object.keys(locales) as (keyof typeof locales)[])(
    "renders %s scope exclusions without deriving app traffic",
    (locale) => {
      view(<ModelObservationsPanel {...modelObservationsProps} />, locale);
      const copy = locales[locale].models.shared.inventory;
      expect(screen.getByRole("heading", { name: copy.metrics })).toBeInTheDocument();
      expect(
        screen.getByText(locales[locale].models.shared.subscriptionUsage.selectionHint)
      ).toBeInTheDocument();
      expect(screen.getByText(copy.costUnavailable)).toBeInTheDocument();
    }
  );
});
