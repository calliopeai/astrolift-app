import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
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
import { useExplorerFixture, APP, ENV, SCOPE } from "./explorer-fixtures";
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
  const view = render(
    <NextIntlClientProvider locale="en" messages={en}>
      <Fixture mode="traces" overrides={{ ...overrides, traceId: null }} />
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
  expect(screen.getAllByRole("table")).toHaveLength(1);
  view.rerender(
    <NextIntlClientProvider locale="en" messages={en}>
      <Fixture mode="traces" overrides={overrides} />
    </NextIntlClientProvider>
  );
  expect(
    screen.queryByRole("button", { name: en.TelemetryExplorer.search })
  ).not.toBeInTheDocument();
  expect(screen.queryByText("GET /health")).not.toBeInTheDocument();
  expect(screen.getAllByRole("table")).toHaveLength(1);
  expect(screen.getByText("OWNED_OPERATION")).toBeVisible();
});

const selectedTrace = "b".repeat(32);
const retainedTraces: NonNullable<ExplorerProps["traces"]> = {
  reason: "OK",
  scope: SCOPE,
  truncated: true,
  items: [
    {
      traceId: selectedTrace,
      rootOperation: "SEARCH_OPERATION_LITERAL",
      rootService: "SEARCH_SERVICE_LITERAL",
      durationMs: 3,
      spanCount: 1,
      statusCode: "OK",
    },
  ],
};
const selectedSpans: NonNullable<ExplorerProps["spans"]> = {
  reason: "OK",
  scope: SCOPE,
  items: [
    {
      traceId: selectedTrace,
      spanId: "c".repeat(16),
      parentSpanId: null,
      operation: "SELECTED_SPAN_LITERAL",
      service: "DETAIL_SERVICE_LITERAL",
      startTime: "1",
      durationMs: 3,
      statusCode: "OK",
      attributes: {},
      resourceAttributes: {},
    },
  ],
};
function RoundTrip({ onTrace }: { onTrace: (trace: string | null) => void }) {
  const props = useExplorerFixture("traces");
  const [traceId, setTraceId] = useState<string | null>(null);
  return (
    <TelemetryExplorerScreen
      {...props}
      traces={retainedTraces}
      spans={selectedSpans}
      traceId={traceId}
      filter="retained-applied-service"
      onTrace={(value) => {
        const nextTrace = typeof value === "function" ? value(traceId) : value;
        onTrace(nextTrace);
        setTraceId(nextTrace);
      }}
    />
  );
}
it.each(locales)(
  "$locale back returns to the one retained result list and unsent filter draft",
  async ({ locale, messages }) => {
    const user = userEvent.setup();
    const onTrace = vi.fn();
    render(
      <NextIntlClientProvider locale={locale} messages={messages}>
        <RoundTrip onTrace={onTrace} />
      </NextIntlClientProvider>
    );
    expect(screen.getAllByRole("table")).toHaveLength(1);
    fireEvent.change(screen.getByLabelText(messages.TelemetryExplorer.service), {
      target: { value: "retained-unsent-draft" },
    });
    await user.click(screen.getByRole("button", { name: "SEARCH_OPERATION_LITERAL" }));
    expect(onTrace).toHaveBeenLastCalledWith(selectedTrace);
    expect(screen.getAllByRole("table")).toHaveLength(1);
    expect(within(screen.getByRole("table")).getByText("SELECTED_SPAN_LITERAL")).toBeVisible();
    expect(screen.queryByText("SEARCH_OPERATION_LITERAL")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: messages.TelemetryExplorer.search })
    ).not.toBeInTheDocument();
    const back = screen.getByRole("button", { name: messages.TelemetryExplorer.backToResults });
    back.focus();
    expect(back).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(onTrace).toHaveBeenLastCalledWith(null);
    expect(screen.getAllByRole("table")).toHaveLength(1);
    expect(within(screen.getByRole("table")).getByText("SEARCH_OPERATION_LITERAL")).toBeVisible();
    expect(screen.queryByText("SELECTED_SPAN_LITERAL")).not.toBeInTheDocument();
    expect(screen.getByLabelText(messages.TelemetryExplorer.service)).toHaveValue(
      "retained-unsent-draft"
    );
    expect(screen.getByText(messages.TelemetryExplorer.truncated)).toBeVisible();
  }
);
it.each(["loading", "failed", "empty"] as const)(
  "selected trace %s never renders search results alongside detail",
  (state) => {
    render(
      <NextIntlClientProvider locale="en" messages={en}>
        <Fixture
          mode="traces"
          overrides={{
            traces: retainedTraces,
            traceId: selectedTrace,
            spans:
              state === "empty" ? { ...selectedSpans, reason: "NO_DATA_YET", items: [] } : null,
            spansLoading: state === "loading",
            spansError: state === "failed" ? "RAW_SPAN_READ_ERROR" : null,
          }}
        />
      </NextIntlClientProvider>
    );
    expect(screen.queryByText("SEARCH_OPERATION_LITERAL")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: en.TelemetryExplorer.search })
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: en.TelemetryExplorer.backToResults })).toBeEnabled();
    if (state === "failed") expect(screen.getByText("RAW_SPAN_READ_ERROR")).toBeVisible();
    expect(screen.queryAllByRole("table").length).toBeLessThanOrEqual(1);
  }
);
it.each(locales)(
  "$locale detail network and backend retries use the selected trace callback only",
  async ({ locale, messages }) => {
    const user = userEvent.setup();
    const retry = vi.fn();
    const refresh = vi.fn();
    const wrapper = (overrides: Partial<ExplorerProps>) => (
      <NextIntlClientProvider locale={locale} messages={messages}>
        <Fixture
          mode="traces"
          overrides={{
            traces: retainedTraces,
            traceId: selectedTrace,
            onRetryTrace: retry,
            onRefresh: refresh,
            ...overrides,
          }}
        />
      </NextIntlClientProvider>
    );
    const view = render(wrapper({ spansError: "RAW_DETAIL_DIAGNOSTIC" }));
    expect(screen.getByText("RAW_DETAIL_DIAGNOSTIC")).toBeVisible();
    await user.click(screen.getByRole("button", { name: messages.TelemetryExplorer.retry }));
    expect(retry).toHaveBeenCalledOnce();
    view.rerender(wrapper({ spans: { reason: "ERROR", items: [], scope: SCOPE } }));
    expect(screen.getByText(messages.TelemetryExplorer.backendError)).toBeVisible();
    await user.click(screen.getByRole("button", { name: messages.TelemetryExplorer.retry }));
    expect(retry).toHaveBeenCalledTimes(2);
    expect(refresh).not.toHaveBeenCalled();
    expect(screen.queryByText("SEARCH_OPERATION_LITERAL")).not.toBeInTheDocument();
  }
);
it("app chooser is exclusive even if supplied old trace/result/detail observations", () => {
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <Fixture
        mode="traces"
        overrides={{
          app: null,
          traces: retainedTraces,
          spans: selectedSpans,
          traceId: selectedTrace,
        }}
      />
    </NextIntlClientProvider>
  );
  expect(screen.getAllByRole("table")).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Example app" })).toBeVisible();
  expect(screen.queryByText("SEARCH_OPERATION_LITERAL")).not.toBeInTheDocument();
  expect(screen.queryByText("SELECTED_SPAN_LITERAL")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: en.TelemetryExplorer.backToResults })
  ).not.toBeInTheDocument();
});
it.each([
  { label: "app", changed: { app: { ...APP, id: "other-app-guid" } } },
  { label: "environment", changed: { environment: { ...ENV, id: "other-env-guid" } } },
  { label: "cluster", changed: { environment: { ...ENV, clusterId: "other-cluster-guid" } } },
  { label: "actor epoch", changed: { binding: "actor:org-guid:2" } },
  { label: "withdrawn app", changed: { app: null } },
  { label: "withdrawn environment", changed: { environment: null } },
] satisfies { label: string; changed: Partial<ExplorerProps> }[])(
  "discards an unsent draft on $label change without resurrecting it on return",
  ({ changed }) => {
    const wrapper = (overrides: Partial<ExplorerProps>) => (
      <NextIntlClientProvider locale="en" messages={en}>
        <Fixture
          mode="traces"
          overrides={{ traces: retainedTraces, filter: "applied", ...overrides }}
        />
      </NextIntlClientProvider>
    );
    const view = render(wrapper({}));
    fireEvent.change(screen.getByLabelText(en.TelemetryExplorer.service), {
      target: { value: "PRIVATE_UNSENT_OLD_CONTEXT" },
    });
    view.rerender(wrapper(changed));
    const input = screen.queryByLabelText(en.TelemetryExplorer.service);
    if (input) expect(input).toHaveValue("applied");
    view.rerender(wrapper({}));
    expect(screen.getByLabelText(en.TelemetryExplorer.service)).toHaveValue("applied");
  }
);
