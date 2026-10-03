import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import pt from "@/messages/pt-BR.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import { TelemetryExplorerScreen } from "./TelemetryExplorerScreen";
import { useExplorerFixture, SCOPE } from "./explorer-fixtures";
import type { ExplorerProps } from "./use-scoped-explorer";
const locales = [
  { locale: "en", messages: en },
  { locale: "es", messages: es },
  { locale: "fr", messages: fr },
  { locale: "de", messages: de },
  { locale: "pt-BR", messages: pt },
  { locale: "ja", messages: ja },
  { locale: "ko", messages: ko },
  { locale: "zh-Hans", messages: zh },
];
afterEach(cleanup);
function Fixture({
  mode,
  overrides = {},
}: {
  mode: "logs" | "traces";
  overrides?: Partial<ExplorerProps>;
}) {
  const props = useExplorerFixture(mode);
  return <TelemetryExplorerScreen {...props} {...overrides} />;
}
it.each(locales)(
  "renders truthful setup and scoped live navigation in $locale",
  ({ locale, messages }) => {
    const view = render(
      <NextIntlClientProvider locale={locale} messages={messages}>
        <Fixture mode="logs" />
      </NextIntlClientProvider>
    );
    expect(screen.getByText(messages.TelemetryExplorer.logsSetup)).toBeVisible();
    expect(screen.queryByText(messages.TelemetryExplorer.empty)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: messages.TelemetryExplorer.liveLogs })).toHaveAttribute(
      "href",
      "/apps/example/logs?section=metrics&panel=pods&env=production"
    );
    view.rerender(
      <NextIntlClientProvider locale={locale} messages={messages}>
        <Fixture mode="traces" />
      </NextIntlClientProvider>
    );
    expect(screen.getByText(messages.TelemetryExplorer.tracesSetup)).toBeVisible();
    expect(screen.getByText(messages.TelemetryExplorer.boundedSearch)).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  }
);
it.each(locales)(
  "distinguishes unavailable placement, empty and errors without changing diagnostics in $locale",
  ({ locale, messages }) => {
    const wrapper = (overrides: Partial<ExplorerProps>) => (
      <NextIntlClientProvider locale={locale} messages={messages}>
        <Fixture mode="logs" overrides={overrides} />
      </NextIntlClientProvider>
    );
    const view = render(
      wrapper({
        logs: {
          reason: "NOT_CONFIGURED",
          scope: null,
          items: [],
          nextCursor: null,
          historicalAvailable: false,
          reachedRetention: false,
        },
      })
    );
    expect(screen.getByText(messages.TelemetryExplorer.scopeUnavailable)).toBeVisible();
    expect(screen.queryByText(messages.TelemetryExplorer.logsSetup)).not.toBeInTheDocument();
    view.rerender(
      wrapper({
        logs: {
          reason: "NO_DATA_YET",
          scope: SCOPE,
          items: [],
          nextCursor: null,
          historicalAvailable: true,
          reachedRetention: true,
        },
      })
    );
    expect(screen.getByText(messages.TelemetryExplorer.empty)).toBeVisible();
    expect(screen.getByText(messages.TelemetryExplorer.retention)).toBeVisible();
    const retry = vi.fn();
    view.rerender(wrapper({ error: "RAW_BACKEND_DIAGNOSTIC: 403 :: unchanged", onRefresh: retry }));
    expect(screen.getByText("RAW_BACKEND_DIAGNOSTIC: 403 :: unchanged")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: messages.TelemetryExplorer.retry }));
    expect(retry).toHaveBeenCalledOnce();
  }
);
it("search and refresh invoke scoped handlers and render only supplied spans", () => {
  const onSearch = vi.fn();
  const onRefresh = vi.fn();
  const traceId = "a".repeat(32);
  const overrides: Partial<ExplorerProps> = {
    onSearch,
    onRefresh,
    traceId,
    traces: {
      reason: "OK",
      scope: SCOPE,
      truncated: true,
      items: [
        {
          traceId,
          rootService: "owned",
          rootOperation: "GET /health",
          durationMs: 5,
          spanCount: 1,
          statusCode: "OK",
        },
      ],
    },
    spans: {
      reason: "OK",
      scope: SCOPE,
      items: [
        {
          traceId,
          spanId: "1".repeat(16),
          parentSpanId: null,
          operation: "OWNED_OPERATION",
          service: "owned",
          startTime: "1",
          durationMs: 5,
          statusCode: "OK",
          attributes: {},
        },
      ],
    },
  };
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <Fixture mode="traces" overrides={overrides} />
    </NextIntlClientProvider>
  );
  fireEvent.change(screen.getByLabelText(en.TelemetryExplorer.service), {
    target: { value: "owned-service" },
  });
  fireEvent.click(screen.getByRole("button", { name: en.TelemetryExplorer.search }));
  expect(onSearch).toHaveBeenCalledWith("owned-service");
  fireEvent.click(screen.getByRole("button", { name: en.TelemetryExplorer.refresh }));
  expect(onRefresh).toHaveBeenCalledOnce();
  expect(screen.getByText(en.TelemetryExplorer.truncated)).toBeVisible();
  expect(screen.getByText("OWNED_OPERATION")).toBeVisible();
});
