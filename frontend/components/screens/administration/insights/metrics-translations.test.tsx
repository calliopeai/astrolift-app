import { fireEvent, render, screen } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";

import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";

import { AdminMetricsScreen } from "./AdminMetricsScreen";
import { PrometheusPanel, SystemMetricsPanel } from "./ClusterMetricsPanels";
import { METRICS, PROMETHEUS_NO_ENDPOINT, SYSTEM_NOT_SUPPORTED } from "./fixtures";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };

describe("platform metrics localization", () => {
  it.each([
    ["fr", "24 h", "Ouvrir l’état du cluster →", "Aucun agent"],
    ["ja", "24時間", "クラスターの状態を開く →", "エージェントなし"],
  ] as const)(
    "keeps %s window selection and offline status links functional",
    (locale, window, status, noAgent) => {
      const onWindowChange = vi.fn();
      const onError = vi.fn();
      render(
        <NextIntlClientProvider locale={locale} messages={catalogs[locale]} onError={onError}>
          <AdminMetricsScreen
            {...METRICS}
            onWindowChange={onWindowChange}
            renderLivePanels={() => null}
          />
        </NextIntlClientProvider>
      );
      fireEvent.click(screen.getByRole("button", { name: window }));
      expect(onWindowChange).toHaveBeenCalledWith("24h");
      expect(
        screen.getAllByRole("link", { name: status }).map((link) => link.getAttribute("href"))
      ).toEqual(["/clusters/onprem-lab/status", "/clusters/new-aks/status"]);
      expect(screen.getByText(noAgent)).toBeInTheDocument();
      expect(onError).not.toHaveBeenCalled();
    }
  );

  it("renders Korean unavailable panels with actual identifiers and links", () => {
    const onError = vi.fn();
    render(
      <NextIntlClientProvider locale="ko" messages={ko} onError={onError}>
        <PrometheusPanel {...PROMETHEUS_NO_ENDPOINT} />
        <SystemMetricsPanel {...SYSTEM_NOT_SUPPORTED} />
      </NextIntlClientProvider>
    );
    expect(screen.getByText("Prometheus 엔드포인트 없음")).toBeInTheDocument();
    expect(screen.getByText(/onprem 드라이버/)).toBeInTheDocument();
    expect(screen.getByText(/prometheus_endpoint/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "클러스터 설정 열기 →" })).toHaveAttribute(
      "href",
      "/clusters/conflict-astro/settings"
    );
    expect(onError).not.toHaveBeenCalled();
  });

  it.each(Object.entries(catalogs))(
    "formats %s plurals, numeric arguments, scope and series messages",
    (locale, messages) => {
      const onError = vi.fn();
      const t = createTranslator({
        locale,
        messages,
        namespace: "administration.metrics",
        onError,
      });
      const { series: _series, ...copy } = en.administration.metrics;
      for (const key of Object.keys(copy) as Array<keyof typeof copy>) {
        expect(
          t(key, {
            seconds: 1,
            hours: 24,
            namespace: "sales-agents",
            source: "CloudWatch ALB",
            provider: "aws-eks",
          })
        ).toBeTruthy();
        if (locale !== "en" && key !== "offline" && key !== "clusters")
          expect(messages.administration.metrics[key]).not.toBe(copy[key]);
      }
      expect(t("offlineAge", { seconds: 7200 })).not.toContain("{seconds");
      expect(t("scope", { source: "CloudWatch ALB", namespace: "sales-agents" })).toContain(
        "sales-agents"
      );
      const series = createTranslator({
        locale,
        messages,
        namespace: "administration.metrics.series",
        onError,
      });
      for (const key of Object.keys(en.administration.metrics.series) as Array<
        keyof typeof en.administration.metrics.series
      >)
        expect(series(key)).toBeTruthy();
      expect(onError).not.toHaveBeenCalled();
    }
  );
});
