import { readFileSync } from "node:fs";
import { parse as parseIcu } from "@formatjs/icu-messageformat-parser";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { StatusMetricsCard } from "./ClusterStatusScreen";
import { METRICS } from "./fixtures";
import type { RangeSeries } from "./types";

const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const LABELS = [
  ["node_count", "Nodes", "count", "nodes"],
  ["pod_running_ratio", "Pods running", "ratio", "podsRunning"],
  ["cpu_utilization", "CPU utilization", "ratio", "cpu"],
  ["memory_utilization", "Memory utilization", "ratio", "memory"],
  ["deployment_ready_ratio", "Deployments ready", "ratio", "deploymentsReady"],
  ["latency_p99", "Apiserver p99", "seconds", "latency"],
  ["network_rx", "Network receive", "bytes_per_sec", "network"],
  ["restart_rate", "Restarts / min", "count", "restartRate"],
] as const;
type Props = React.ComponentProps<typeof StatusMetricsCard>;
function view(locale: string, overrides: Partial<Props> = {}) {
  return (
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      now={new Date("2026-10-01T17:00:00Z")}
      timeZone="America/Costa_Rica"
    >
      <StatusMetricsCard slug="RAW_CLUSTER_SLUG" {...METRICS} {...overrides} />
    </NextIntlClientProvider>
  );
}
function series(overrides: Partial<RangeSeries> = {}): RangeSeries {
  return {
    metric: "RAW_FUTURE_METRIC",
    label: "RAW_METRIC_LABEL",
    unit: "count",
    current: 0,
    points: [],
    ...overrides,
  };
}
function withSeries(row: RangeSeries): Partial<Props> {
  return { range: { ...METRICS.range!, series: [row] } };
}
function metricValue() {
  return screen.getByTitle("RAW_METRIC_LABEL").parentElement!.querySelector("p")!;
}

describe.each(locales)("cluster Status metrics in %s", (locale) => {
  const t = createTranslator({ locale, messages: catalogs[locale], namespace: "clusterMetrics" });
  const number = (value: number, options?: Intl.NumberFormatOptions) =>
    new Intl.NumberFormat(locale, options).format(value);

  it("has all metrics messages with valid ICU syntax", () => {
    expect(Object.keys(catalogs[locale].clusterMetrics).sort()).toEqual(
      Object.keys(catalogs.en.clusterMetrics).sort()
    );
    expect(Object.keys(catalogs[locale].clusterMetrics)).toHaveLength(38);
    for (const value of Object.values(catalogs[locale].clusterMetrics))
      expect(() => parseIcu(value as string)).not.toThrow();
    if (locale !== "en")
      for (const key of ["title", "description", "unavailableHelp", "cachedFailed"])
        expect(catalogs[locale].clusterMetrics[key]).not.toEqual(catalogs.en.clusterMetrics[key]);
  });

  it.each(LABELS)(
    "translates exact default label %s while preserving the raw label title",
    (metric, label, unit, key) => {
      render(view(locale, withSeries(series({ metric, label, unit }))));
      expect(screen.getByTitle(label)).toHaveTextContent(t(key));
      expect(screen.getByRole("heading", { name: t("title") })).toBeInTheDocument();
      expect(screen.getByText(t("description"))).toBeInTheDocument();
    }
  );

  it.each(["RAW_FUTURE_METRIC", "__proto__", "constructor"])(
    "keeps unknown metric %s neutral",
    (metric) => {
      render(view(locale, withSeries(series({ metric, unit: "ratio", current: 0.99 }))));
      expect(screen.getByTitle("RAW_METRIC_LABEL")).toHaveTextContent("RAW_METRIC_LABEL");
      expect(metricValue()).toHaveClass("text-foreground");
      expect(metricValue()).not.toHaveClass("text-destructive");
    }
  );

  it.each([
    ["RAW_CUSTOM_PROVIDER_LABEL", "ratio"],
    ["CPU utilization", "RAW_FUTURE_UNIT"],
  ])("retains non-default provider label/unit %s/%s", (label, unit) => {
    render(view(locale, withSeries(series({ metric: "cpu_utilization", label, unit }))));
    expect(screen.getByTitle(label)).toHaveTextContent(label);
  });

  it.each([-1, NaN, Infinity, Number.MAX_VALUE])(
    "keeps invalid or overflowing ratio %s unknown and neutral",
    (current) => {
      render(
        view(locale, withSeries(series({ metric: "cpu_utilization", unit: "ratio", current })))
      );
      expect(metricValue()).toHaveTextContent(t("unknown"));
      expect(metricValue()).toHaveClass("text-foreground");
    }
  );

  it("shows null observations as missing and preserves real zero", () => {
    const { rerender } = render(view(locale, withSeries(series({ current: null }))));
    expect(metricValue()).toHaveTextContent("—");
    rerender(view(locale, withSeries(series({ metric: "restart_rate", current: 0 }))));
    expect(metricValue()).toHaveTextContent(number(0));
    expect(metricValue()).toHaveClass("text-success-fg");
  });

  it("retains a fractional restart rate rather than rounding it to zero", () => {
    render(view(locale, withSeries(series({ metric: "restart_rate", current: 0.01 }))));
    expect(metricValue().textContent).toBe(number(0.01, { maximumFractionDigits: 2 }));
    expect(metricValue()).toHaveClass("text-warning-fg");
  });

  it.each([0.001, 0.000001, Number.MIN_VALUE])(
    "retains a nonzero restart rate %s below the normal display precision",
    (current) => {
      render(view(locale, withSeries(series({ metric: "restart_rate", current }))));
      expect(metricValue().textContent).toBe(
        number(current, {
          notation: current < 0.001 ? "scientific" : "standard",
          maximumSignificantDigits: 2,
        })
      );
      expect(metricValue().textContent).not.toBe(number(0));
    }
  );

  it.each([0.5, -1, Number.MAX_SAFE_INTEGER + 1])(
    "does not present invalid node count %s as a census",
    (current) => {
      render(view(locale, withSeries(series({ metric: "node_count", current }))));
      expect(metricValue()).toHaveTextContent(t("unknown"));
      expect(metricValue()).toHaveClass("text-foreground");
    }
  );

  it("keeps overcommitted request ratios distinct from malformed readiness ratios", () => {
    const { rerender } = render(
      view(locale, withSeries(series({ metric: "cpu_utilization", unit: "ratio", current: 1.5 })))
    );
    expect(metricValue().textContent).toBe(
      number(1.5, { style: "percent", minimumFractionDigits: 1, maximumFractionDigits: 1 })
    );
    expect(metricValue()).toHaveClass("text-destructive");
    rerender(
      view(locale, withSeries(series({ metric: "pod_running_ratio", unit: "ratio", current: 1.5 })))
    );
    expect(metricValue()).toHaveTextContent(t("unknown"));
    expect(metricValue()).toHaveClass("text-foreground");
  });

  it.each([
    [0.00004, "seconds", "microseconds", 40, 0],
    [0.5, "seconds", "milliseconds", 500, 0],
    [1.25, "seconds", "seconds", 1.25, 2],
    [0, "bytes_per_sec", "bytes", 0, 0],
    [4096, "bytes_per_sec", "kibibytes", 4, 1],
    [1048576, "bytes_per_sec", "mebibytes", 1, 1],
    [1073741824, "bytes_per_sec", "gibibytes", 1, 2],
  ] as const)(
    "formats observed %s %s with locale numbers and the matching unit",
    (current, unit, key, value, decimals) => {
      render(view(locale, withSeries(series({ current, unit }))));
      expect(metricValue().textContent).toBe(
        t(key, {
          value: number(value, {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals,
          }),
        })
      );
    }
  );

  it.each([
    ["no_endpoint", "noEndpoint"],
    ["cluster_internal_endpoint", "internal"],
    ["unreachable", "unreachable"],
    ["RAW_FUTURE_REASON", "unavailable"],
    ["__proto__", "unavailable"],
    [null, "unavailable"],
    ["", "unavailable"],
  ] as const)("uses only the observed unavailable reason %j", (reason, kind) => {
    render(
      view(locale, {
        instant: { ...METRICS.instant!, available: false, reason },
        range: { ...METRICS.range!, available: false, reason },
      })
    );
    expect(screen.getAllByText(t(`${kind}Title`))).toHaveLength(2);
    expect(screen.getAllByText(t(`${kind}Help`))).toHaveLength(2);
    if (reason) expect(screen.getAllByText(reason)).toHaveLength(2);
    if (kind === "unavailable")
      expect(screen.queryByText(t("unreachableTitle"))).not.toBeInTheDocument();
    for (const link of screen.getAllByRole("link"))
      expect(link).toHaveAttribute("href", "/clusters/RAW_CLUSTER_SLUG/settings");
  });

  it("does not copy an instant failure diagnosis into a range report with no reason", () => {
    render(
      view(locale, {
        instant: { ...METRICS.instant!, available: false, reason: "no_endpoint" },
        range: { ...METRICS.range!, available: false, reason: null },
      })
    );
    expect(screen.getByText(t("noEndpointTitle"))).toBeInTheDocument();
    expect(screen.getByText(t("unavailableTitle"))).toBeInTheDocument();
  });

  it("keeps selected window tokens and exact callbacks while translating button labels", async () => {
    const onWindowChange = vi.fn();
    const user = userEvent.setup();
    render(view(locale, { onWindowChange }));
    expect(screen.getByRole("button", { name: t("hours", { count: 1 }) })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    await user.click(screen.getByRole("button", { name: t("days", { count: 3 }) }));
    expect(onWindowChange).toHaveBeenCalledExactlyOnceWith("3d");
  });

  it("retains reports through read failures and exposes the real retry without automatically writing", async () => {
    const refetch = vi.fn();
    const user = userEvent.setup();
    render(view(locale, { error: "RAW_METRICS_READ_DIAGNOSTIC", refetch }));
    expect(screen.getByRole("alert")).toHaveTextContent(t("cachedFailed"));
    expect(screen.getByText("RAW_METRICS_READ_DIAGNOSTIC")).toBeInTheDocument();
    expect(screen.getByText(t("cpu"))).toBeInTheDocument();
    expect(refetch).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: t("retry") }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it("keeps invalid sample timestamps out of a fabricated historical trend", () => {
    render(
      view(
        locale,
        withSeries(
          series({
            points: [
              { ts: NaN, value: 2 },
              { ts: Number.MAX_VALUE, value: 3 },
            ],
          })
        )
      )
    );
    expect(screen.getByText(t("noData"))).toBeInTheDocument();
    expect(screen.queryByText(t("collecting"))).not.toBeInTheDocument();
  });
});
