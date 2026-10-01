import { readFileSync } from "node:fs";
import path from "node:path";
import { parse, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useLocalListState } from "@/components/list/use-list-state";
import { locales } from "@/i18n/config";
import { PendingGatesScreen } from "../approvals/PendingGates";
import { GATE, PENDING_GATES } from "../approvals/approvals-b.fixtures";
import { METRICS, NO_ROLLOUTS_METRICS } from "../ops/metrics-ops-shell.fixtures";
import { MetricsScreen, type MetricsScreenProps } from "./MetricsScreen";
import { METRICS_APPS_LIST } from "./metrics-apps-list";

const catalogs = Object.fromEntries(
  locales.map((locale) => [
    locale,
    JSON.parse(readFileSync(path.resolve("messages", `${locale}.json`), "utf8")),
  ])
);
const now = new Date("2026-09-30T12:00:00Z");

function Metrics(props: Partial<MetricsScreenProps>) {
  const list = useLocalListState(METRICS_APPS_LIST);
  return <MetricsScreen {...METRICS} {...props} list={list} />;
}

function provider(locale: string, children: React.ReactNode, errors = vi.fn()) {
  return (
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      timeZone="America/Los_Angeles"
      now={now}
      onError={errors}
    >
      {children}
    </NextIntlClientProvider>
  );
}

function hydrationText(container: HTMLElement) {
  const copy = container.cloneNode(true) as HTMLElement;
  copy.querySelectorAll('[data-slot="select-value"]').forEach((value) => value.remove());
  return copy.textContent;
}

function leaves(tree: Record<string, unknown>, prefix = ""): Record<string, string> {
  return Object.fromEntries(
    Object.entries(tree).flatMap(([key, value]) => {
      const name = prefix ? `${prefix}.${key}` : key;
      return typeof value === "string"
        ? [[name, value]]
        : Object.entries(leaves(value as Record<string, unknown>, name));
    })
  );
}

function argumentsOf(nodes: MessageFormatElement[]): string[] {
  return [
    ...new Set(
      nodes.flatMap((node): string[] => {
        if (node.type === 0 || node.type === 7) return [];
        if (node.type === 8) return [`tag:${node.value}`, ...argumentsOf(node.children)];
        if (node.type === 5 || node.type === 6)
          return [
            `${node.type}:${node.value}`,
            ...Object.values(node.options).flatMap((option) => argumentsOf(option.value)),
          ];
        return [`${node.type}:${node.value}`];
      })
    ),
  ].sort();
}

describe("metrics and pending review presentation", () => {
  beforeEach(() => localStorage.clear());
  it.each(locales)("%s preserves ICU contracts and translates complete presentation", (locale) => {
    for (const [namespace, tree] of [
      ["metrics", catalogs.en.metrics],
      ["approvals.pendingGates", catalogs.en.approvals.pendingGates],
    ] as const) {
      const translatedTree =
        namespace === "metrics"
          ? catalogs[locale].metrics
          : catalogs[locale].approvals.pendingGates;
      const canonical = leaves(tree),
        translated = leaves(translatedTree);
      expect(Object.keys(translated).sort()).toEqual(Object.keys(canonical).sort());
      const errors = vi.fn();
      const t = createTranslator({
        locale,
        messages: catalogs[locale],
        namespace,
        onError: errors,
      });
      for (const [key, copy] of Object.entries(canonical)) {
        expect(argumentsOf(parse(translated[key]))).toEqual(argumentsOf(parse(copy)));
        expect(
          t(key, {
            label: "RAW_LABEL",
            seconds: 17,
            minutes: 2,
            days: 30,
            succeeded: 1234,
            total: 2345,
            count: 1234,
            time: "RAW_TIME",
            names: "RAW_NAMES",
          })
        ).not.toMatch(/\{(?:label|seconds|minutes|days|succeeded|total|count|time|names)[,}]/);
      }
      expect(errors).not.toHaveBeenCalled();
    }
    if (locale !== "en") {
      expect(catalogs[locale].metrics.description).not.toBe(catalogs.en.metrics.description);
      expect(catalogs[locale].approvals.pendingGates.description).not.toBe(
        catalogs.en.approvals.pendingGates.description
      );
    }
  });

  it.each(locales)(
    "%s retains actual rates, status identities, filtering and routes",
    async (locale) => {
      const errors = vi.fn();
      render(provider(locale, <Metrics temporalUiUrl="https://workflows.example.test/" />, errors));
      expect(
        screen.getByRole("heading", { name: catalogs[locale].metrics.title })
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          new Intl.NumberFormat(locale, {
            style: "percent",
            minimumFractionDigits: 1,
            maximumFractionDigits: 1,
          }).format(37 / 40),
          { normalizer: (text) => text }
        )
      ).toBeInTheDocument();
      expect(screen.getByRole("link", { name: /Storefront/ })).toHaveAttribute(
        "href",
        "/apps/storefront"
      );
      expect(screen.getByRole("link", { name: /Support agent/ })).toHaveAttribute(
        "href",
        "/agents/support-agent"
      );
      expect(
        screen.getByRole("link", { name: catalogs[locale].metrics.temporalUi })
      ).toHaveAttribute("href", "https://workflows.example.test/");
      expect(
        screen.getByText(catalogs[locale].apps.overview.latestDeploy.status.pending_approval)
      ).toBeInTheDocument();
      fireEvent.change(screen.getByPlaceholderText(catalogs[locale].metrics.searchPlaceholder), {
        target: { value: "billing-api" },
      });
      expect(screen.getByRole("link", { name: /Billing API/ })).toHaveAttribute(
        "href",
        "/apps/billing-api"
      );
      await waitFor(() =>
        expect(screen.queryByRole("link", { name: /Storefront/ })).not.toBeInTheDocument()
      );
      expect(screen.getByText("sha-4f1c9e2")).toBeInTheDocument();
      expect(errors).not.toHaveBeenCalled();
    }
  );

  it.each(locales)(
    "%s distinguishes missing rate from a measured zero and read failure from emptiness",
    (locale) => {
      const retryMetrics = vi.fn(),
        retryHealth = vi.fn();
      const view = render(provider(locale, <Metrics metrics={NO_ROLLOUTS_METRICS} apps={[]} />));
      expect(
        screen.getByRole("img", { name: catalogs[locale].metrics.successRateUnavailable })
      ).toHaveTextContent("—");
      view.rerender(
        provider(locale, <Metrics metrics={{ ...NO_ROLLOUTS_METRICS, successRate: 0 }} apps={[]} />)
      );
      expect(
        screen.queryByRole("img", { name: catalogs[locale].metrics.successRateUnavailable })
      ).not.toBeInTheDocument();
      expect(
        screen.getByText(
          new Intl.NumberFormat(locale, {
            style: "percent",
            minimumFractionDigits: 1,
            maximumFractionDigits: 1,
          }).format(0),
          { normalizer: (text) => text }
        )
      ).toBeInTheDocument();
      view.rerender(
        provider(
          locale,
          <Metrics
            metrics={undefined}
            apps={[]}
            metricsError="RAW_METRICS_DENIAL"
            healthError="RAW_HEALTH_DENIAL"
            onRetryMetrics={retryMetrics}
            onRetryHealth={retryHealth}
          />
        )
      );
      expect(screen.getByText("RAW_METRICS_DENIAL")).toBeInTheDocument();
      expect(screen.getByText("RAW_HEALTH_DENIAL")).toBeInTheDocument();
      expect(screen.queryByText(catalogs[locale].metrics.emptyTitle)).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: catalogs[locale].metrics.retryMetrics }));
      fireEvent.click(screen.getByRole("button", { name: catalogs[locale].shared.list.retry }));
      expect(retryMetrics).toHaveBeenCalledTimes(1);
      expect(retryHealth).toHaveBeenCalledTimes(1);
    }
  );

  it.each(locales)(
    "%s keeps review identities, original diagnostics and refresh functional",
    (locale) => {
      const refresh = vi.fn(),
        errors = vi.fn();
      const gate = {
        ...GATE,
        definitionSlug: "release/日本",
        runGuid: "run&exact",
        stageRole: "RAW_ROLE",
        stageApprovers: ["RAW_TEAM", "RAW_USER"],
      };
      const view = render(
        provider(
          locale,
          <PendingGatesScreen {...PENDING_GATES} gates={[gate]} onRefresh={refresh} />,
          errors
        )
      );
      expect(
        screen.getByRole("link", { name: catalogs[locale].approvals.pendingGates.review })
      ).toHaveAttribute("href", "/workflows/release%2F%E6%97%A5%E6%9C%AC/observe?run=run%26exact");
      expect(screen.getByText("RAW_ROLE")).toBeInTheDocument();
      expect(screen.getByText(/RAW_TEAM/)).toHaveTextContent("RAW_USER");
      fireEvent.click(
        screen.getByRole("button", { name: catalogs[locale].approvals.pendingGates.refresh })
      );
      expect(refresh).toHaveBeenCalledTimes(1);
      view.rerender(
        provider(
          locale,
          <PendingGatesScreen {...PENDING_GATES} gates={[]} error="RAW_REVIEW_DENIAL" />,
          errors
        )
      );
      expect(screen.getByText("RAW_REVIEW_DENIAL")).toBeInTheDocument();
      expect(
        screen.queryByText(catalogs[locale].approvals.pendingGates.emptyTitle)
      ).not.toBeInTheDocument();
      expect(errors).not.toHaveBeenCalled();
    }
  );

  it.each(locales)(
    "%s hydrates review ages and metric dates from the same request context",
    async (locale) => {
      const errors = vi.fn(),
        recoverable = vi.fn();
      const tree = provider(
        locale,
        <>
          <Metrics />
          <PendingGatesScreen {...PENDING_GATES} />
        </>,
        errors
      );
      const container = document.createElement("div");
      document.body.appendChild(container);
      container.innerHTML = renderToString(tree);
      // Radix Select fills its trigger value after mounting. Dates, relative
      // ages, and links must stay identical across hydration.
      const before = {
        text: hydrationText(container),
        times: Array.from(container.querySelectorAll("time"), (element) => element.outerHTML),
        links: Array.from(container.querySelectorAll("a"), (element) => element.outerHTML),
      };
      let root: ReturnType<typeof hydrateRoot> | undefined;
      try {
        await act(async () => {
          root = hydrateRoot(container, tree, { onRecoverableError: recoverable });
        });
        expect(hydrationText(container)).toBe(before.text);
        expect(
          Array.from(container.querySelectorAll("time"), (element) => element.outerHTML)
        ).toEqual(before.times);
        expect(Array.from(container.querySelectorAll("a"), (element) => element.outerHTML)).toEqual(
          before.links
        );
        expect(errors).not.toHaveBeenCalled();
        expect(recoverable).not.toHaveBeenCalled();
      } finally {
        await act(async () => root?.unmount());
        container.remove();
      }
    }
  );

  it("changes locale without resetting the selected app filter", async () => {
    const view = render(provider("en", <Metrics />));
    fireEvent.change(screen.getByPlaceholderText(catalogs.en.metrics.searchPlaceholder), {
      target: { value: "billing-api" },
    });
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: /Storefront/ })).not.toBeInTheDocument()
    );
    view.rerender(provider("fr", <Metrics />));
    expect(screen.getByPlaceholderText(catalogs.fr.metrics.searchPlaceholder)).toHaveValue(
      "billing-api"
    );
    expect(screen.getByRole("link", { name: /Billing API/ })).toHaveAttribute(
      "href",
      "/apps/billing-api"
    );
    expect(screen.queryByRole("link", { name: /Storefront/ })).not.toBeInTheDocument();
  });
});
