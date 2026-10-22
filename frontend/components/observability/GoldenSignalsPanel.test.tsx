import { fireEvent, render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import type {
  AstroliftAppNetworkMetrics,
  AstroliftAppNetworkSeries,
} from "@/graphql/__generated__/schema";
import de from "@/messages/de.json";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import pt from "@/messages/pt-BR.json";
import zh from "@/messages/zh-Hans.json";

import { GoldenSignalsPanel, type GoldenSignalsPanelProps } from "./GoldenSignalsPanel";

const locales = { en, de, es, fr, ja, ko, "pt-BR": pt, "zh-Hans": zh };

const series = (
  direction: "RECEIVE" | "TRANSMIT",
  totalBytes: number | null,
  unavailableReason: AstroliftAppNetworkSeries["measurement"]["unavailableReason"] = null
): AstroliftAppNetworkSeries => ({
  direction,
  unit: "bytes_per_second",
  promql: `sum(rate(container_network_${direction.toLowerCase()}_bytes_total{namespace="shop"}[2m]))`,
  reason: unavailableReason ? "ERROR" : "OK",
  totalBytes,
  samples: unavailableReason
    ? []
    : [
        { ts: "2026-10-22T12:00:00Z", value: 2048 },
        { ts: "2026-10-22T12:00:15Z", value: 4096 },
      ],
  measurement: {
    effectiveScope: "APP_ENVIRONMENT",
    source: "CADVISOR",
    identityBasis: "NAMESPACE",
    available: !unavailableReason,
    unavailableReason,
    target: {
      organizationId: "org-guid",
      appId: "app-guid",
      appSlug: "shop",
      environmentId: "environment-guid",
      environmentName: "production",
      clusterId: "cluster-guid",
      namespace: "shop-production",
    },
    containers: [],
    usageSamples: [],
    limitSamples: [],
  },
});

const metrics = (rows: AstroliftAppNetworkSeries[], reason = "OK"): AstroliftAppNetworkMetrics => ({
  reason: reason as AstroliftAppNetworkMetrics["reason"],
  rangeSeconds: 3600,
  stepSeconds: 15,
  rateWindowSeconds: 120,
  sourcedAt: "2026-10-22T12:01:00Z",
  series: rows,
});

const props = (overrides: Partial<GoldenSignalsPanelProps>): GoldenSignalsPanelProps => ({
  range: "1h",
  onRangeChange: () => {},
  signals: [],
  reason: "OK",
  loading: false,
  onRetry: () => {},
  statusBreakdown: null,
  statusLoading: false,
  onStatusRetry: () => {},
  network: null,
  networkReason: null,
  networkLoading: false,
  onNetworkRetry: () => {},
  ...overrides,
});

function renderPanel(overrides: Partial<GoldenSignalsPanelProps>, locale = "en") {
  const messages = locales[locale as keyof typeof locales];
  return render(
    <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC">
      <GoldenSignalsPanel {...props(overrides)} />
    </NextIntlClientProvider>
  );
}

describe("network I/O card", () => {
  for (const [locale, messages] of Object.entries(locales)) {
    it(`labels both directions with their window totals and source in ${locale}`, () => {
      renderPanel(
        {
          network: metrics([series("RECEIVE", 1024 * 1024), series("TRANSMIT", 512)]),
          networkReason: "OK",
        },
        locale
      );
      const copy = messages.obsGoldenSignals.network;
      const sources = messages.apps.observability.signalMeasurement;
      expect(screen.getByText(copy.title)).toBeVisible();
      const received = screen.getByTestId("network-receive");
      expect(within(received).getByText(copy.receive)).toBeVisible();
      expect(
        within(received).getByText(copy.total.replace("{bytes}", "1.0 MiB"))
      ).toBeInTheDocument();
      expect(
        within(received).getByText(sources.source.replace("{source}", sources.sources.CADVISOR))
      ).toBeInTheDocument();
      const sent = screen.getByTestId("network-transmit");
      expect(within(sent).getByText(copy.transmit)).toBeVisible();
      expect(within(sent).getByText(copy.total.replace("{bytes}", "512 B"))).toBeInTheDocument();
    });
  }

  it("shows a failed direction's reason and no total while the other still reads", () => {
    renderPanel({
      network: metrics([series("RECEIVE", 2048), series("TRANSMIT", null, "QUERY_ERROR")]),
      networkReason: "OK",
    });
    const sent = screen.getByTestId("network-transmit");
    expect(within(sent).queryByText(/in this window/)).not.toBeInTheDocument();
    expect(
      within(sent).getByText(en.apps.observability.signalMeasurement.reasons.readError)
    ).toBeVisible();
    expect(
      within(screen.getByTestId("network-receive")).getByText("2.0 KiB in this window")
    ).toBeInTheDocument();
  });

  it("names an ambiguous workload read without calling it a ratio", () => {
    renderPanel({
      network: metrics(
        [series("RECEIVE", null, "AMBIGUOUS_SERIES"), series("TRANSMIT", null, "AMBIGUOUS_SERIES")],
        "ERROR"
      ),
      networkReason: "ERROR",
    });
    const partial = en.apps.observability.signalMeasurement.reasons.partial;
    expect(partial).not.toMatch(/ratio/);
    expect(screen.getAllByText(partial)).toHaveLength(2);
    expect(screen.queryByText(/in this window/)).not.toBeInTheDocument();
  });

  it("says not configured, without a retry, when nothing measures the target", () => {
    const unconfigured = metrics(
      [series("RECEIVE", null, "NOT_CONFIGURED"), series("TRANSMIT", null, "NOT_CONFIGURED")].map(
        (row) => ({ ...row, reason: "NOT_CONFIGURED" as const })
      ),
      "NOT_CONFIGURED"
    );
    renderPanel({ network: unconfigured, networkReason: "NOT_CONFIGURED" });
    expect(screen.getByText(en.obsGoldenSignals.network.notConfigured)).toBeVisible();
    expect(screen.queryByRole("button", { name: en.obsGoldenSignals.tryAgain })).toBeNull();
  });

  it("offers a retry when the read itself failed", () => {
    const onNetworkRetry = vi.fn();
    renderPanel({ network: null, networkReason: "ERROR", onNetworkRetry });
    expect(screen.getByText(en.obsPanelReason.errorTitle)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: en.obsGoldenSignals.tryAgain }));
    expect(onNetworkRetry).toHaveBeenCalledOnce();
  });
});
