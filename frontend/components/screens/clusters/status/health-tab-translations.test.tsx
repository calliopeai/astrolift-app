import { readFileSync } from "node:fs";
import { parse as parseIcu } from "@formatjs/icu-messageformat-parser";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { useLocalListState, type ListState } from "@/components/list/use-list-state";
import { selectRows } from "@/components/list/select-rows";
import { TooltipProvider } from "@/components/ui/tooltip";
import { locales } from "@/i18n/config";
import { ClusterHealthBody, type ClusterHealthBodyProps } from "./ClusterHealthScreen";
import {
  CLUSTER_WORKLOADS_SELECT,
  useClusterWorkloadsListDefinition,
} from "./cluster-workloads-list";
import { HEALTH, WORKLOADS } from "./fixtures";
import type { WorkloadRow } from "./types";

const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const NOW = new Date("2026-10-01T17:00:00Z");
function wrap(locale: string, children: ReactNode) {
  return (
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      now={NOW}
      timeZone="America/Costa_Rica"
    >
      <TooltipProvider>{children}</TooltipProvider>
    </NextIntlClientProvider>
  );
}
function row(overrides: Partial<WorkloadRow> = {}): WorkloadRow {
  return {
    ...WORKLOADS.rows[0],
    workloadName: "RAW_WORKLOAD",
    namespace: "RAW_NAMESPACE",
    readyReplicas: 2,
    desiredReplicas: 3,
    restartCount24h: 1000,
    lastImageDeployedAt: "2026-10-01T16:55:00Z",
    ...overrides,
  };
}
function Body({
  health = HEALTH,
  rows = [row()],
  initial,
  error = null,
  loading = false,
  retry = () => {},
}: {
  health?: ClusterHealthBodyProps["health"];
  rows?: WorkloadRow[];
  initial?: Partial<ListState>;
  error?: string | null;
  loading?: boolean;
  retry?: () => void;
}) {
  const definition = useClusterWorkloadsListDefinition();
  const list = useLocalListState(definition, initial);
  const selected = selectRows(
    rows,
    {
      filters: list.filters,
      q: list.state.q,
      sort: list.state.sort,
      page: list.state.page,
      pageSize: list.state.pageSize,
    },
    CLUSTER_WORKLOADS_SELECT
  );
  return (
    <ClusterHealthBody
      health={health}
      workloads={{
        list,
        ...selected,
        loading,
        error: error ? { message: error } : null,
        onRetry: retry,
        observation: { hasRows: rows.length > 0, loading, error },
      }}
    />
  );
}

describe.each(locales)("full cluster Health tab in %s", (locale) => {
  const t = createTranslator({ locale, messages: catalogs[locale], namespace: "clusterHealthTab" });
  const h = createTranslator({ locale, messages: catalogs[locale], namespace: "clusterHealth" });
  it("renders translated column, filter and warning chrome with literal provider names", () => {
    expect(Object.keys(catalogs[locale].clusterHealthTab).sort()).toEqual(
      Object.keys(catalogs.en.clusterHealthTab).sort()
    );
    for (const value of Object.values(catalogs[locale].clusterHealthTab))
      expect(() => parseIcu(value as string)).not.toThrow();
    if (locale !== "en") expect(t("liveHelp")).not.toEqual(catalogs.en.clusterHealthTab.liveHelp);
    render(wrap(locale, <Body />));
    for (const key of ["workload", "namespace", "readiness", "restarts24h", "deployed"] as const)
      expect(screen.getByRole("columnheader", { name: new RegExp(t(key)) })).toBeInTheDocument();
    expect(screen.getByPlaceholderText(t("search"))).toBeInTheDocument();
    expect(screen.getByText(t("liveHelp"))).toBeInTheDocument();
    expect(screen.getByText(t("workloadHelp"))).toBeInTheDocument();
    expect(screen.getByTitle("RAW_WORKLOAD")).toHaveTextContent("RAW_WORKLOAD");
    const workload = screen.getByRole("region", { name: h("workloadTitle") });
    expect(
      within(workload).getByText(
        (_, element) =>
          element?.tagName === "SPAN" &&
          element.textContent === new Intl.NumberFormat(locale).format(1000)
      )
    ).toBeInTheDocument();
  });
  it.each([
    ["Running", "running"],
    ["Succeeded", "succeeded"],
    ["Pending", "pending"],
    ["Failed", "failed"],
    ["CrashLoopBackOff", "crashLoop"],
  ] as const)("translates %s and keeps the diagnostic token", (phase, key) => {
    render(
      wrap(
        locale,
        <Body health={{ ...HEALTH, pods: [{ namespace: "RAW_NAMESPACE", phase, count: 1000 }] }} />
      )
    );
    expect(screen.getByTitle(phase)).toHaveTextContent(h(key));
    expect(screen.getByTitle(phase).textContent).toContain(
      new Intl.NumberFormat(locale).format(1000)
    );
  });
  it.each(["FuturePhase", "__proto__", "constructor", "running", ""])(
    "keeps unknown phase %j neutral",
    (phase) => {
      render(
        wrap(
          locale,
          <Body health={{ ...HEALTH, pods: [{ namespace: "RAW_NAMESPACE", phase, count: 1 }] }} />
        )
      );
      const pill = document.querySelector('[data-slot="badge"][title]')!;
      expect(pill).toHaveAttribute("title", phase);
      expect(pill).toHaveTextContent(phase || h("unknown"));
      expect(pill).toHaveClass("text-muted-foreground");
    }
  );
  it.each([-1, 0.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1])(
    "keeps invalid observation %s unknown and neutral",
    (value) => {
      render(
        wrap(
          locale,
          <Body
            rows={[row({ readyReplicas: value, restartCount24h: value })]}
            health={{
              ...HEALTH,
              pods: [{ namespace: "RAW_NAMESPACE", phase: "Running", count: value }],
            }}
          />
        )
      );
      const workload = screen.getByRole("region", { name: h("workloadTitle") });
      expect(within(workload).getAllByText(h("unknown"))).toHaveLength(2);
      for (const value of within(workload).getAllByText(h("unknown")))
        expect(value).toHaveClass("text-muted-foreground");
      expect(screen.getByTitle("Running")).toHaveClass("text-muted-foreground");
    }
  );
  it("keeps unknown and zero-sized deployments out of the Unhealthy view", () => {
    render(
      wrap(
        locale,
        <Body
          rows={[
            row({ workloadName: "UNKNOWN_READY", readyReplicas: 4 }),
            row({ workloadName: "ZERO_READY", readyReplicas: 0, desiredReplicas: 0 }),
            row({ workloadName: "CONFIRMED_DOWN", readyReplicas: 0 }),
          ]}
          initial={{ view: "unhealthy" }}
        />
      )
    );
    expect(screen.getByText("CONFIRMED_DOWN")).toBeInTheDocument();
    expect(screen.queryByText("UNKNOWN_READY")).not.toBeInTheDocument();
    expect(screen.queryByText("ZERO_READY")).not.toBeInTheDocument();
  });
  it("offers Unknown and Inactive filters with stable values", async () => {
    render(
      wrap(
        locale,
        <Body
          rows={[
            row({ workloadName: "UNKNOWN_READY", readyReplicas: 4 }),
            row({ workloadName: "ZERO_READY", readyReplicas: 0, desiredReplicas: 0 }),
          ]}
          initial={{ filters: { state: "inactive" } }}
        />
      )
    );
    expect(screen.getByText("ZERO_READY")).toBeInTheDocument();
    expect(screen.getByText("0 / 0")).toHaveClass("text-muted-foreground");
    expect(screen.queryByText("UNKNOWN_READY")).not.toBeInTheDocument();
    expect(screen.getByText(t("inactive"))).toBeInTheDocument();
  });
  it("qualifies empty observations and preserves literal warning diagnostics", () => {
    render(wrap(locale, <Body rows={[]} health={{ ...HEALTH, pods: [], events: [] }} />));
    expect(screen.getByText(h("noHealthHelp"))).toBeInTheDocument();
    expect(screen.getByText(h("noDeploymentsHelp"))).toBeInTheDocument();
    expect(screen.queryByText(/couldn't reach the apiserver/)).not.toBeInTheDocument();
  });
  it("retains reports through failed refreshes and retries each source independently", async () => {
    const healthRetry = vi.fn();
    const workloadsRetry = vi.fn();
    render(
      wrap(
        locale,
        <Body
          error="RAW_WORKLOAD_ERROR"
          retry={workloadsRetry}
          health={{ ...HEALTH, error: "RAW_HEALTH_ERROR", refetch: healthRetry }}
        />
      )
    );
    const live = screen.getByRole("region", { name: h("liveTitle") });
    const workload = screen.getByRole("region", { name: h("workloadTitle") });
    expect(within(workload).getByText("RAW_WORKLOAD")).toBeInTheDocument();
    expect(within(live).getByRole("alert")).toHaveTextContent(h("cachedFailed"));
    expect(within(workload).getByRole("alert")).toHaveTextContent("RAW_WORKLOAD_ERROR");
    const user = userEvent.setup();
    await user.click(within(live).getByRole("button", { name: h("retry") }));
    expect(healthRetry).toHaveBeenCalledTimes(1);
    expect(workloadsRetry).not.toHaveBeenCalled();
    await user.click(within(workload).getByRole("button", { name: h("retry") }));
    expect(workloadsRetry).toHaveBeenCalledTimes(1);
  });
  it("keeps failed observation notices even when the current filter selects no rows", () => {
    render(
      wrap(locale, <Body initial={{ q: "NO_MATCH" }} error="RAW_FILTERED_REFRESH_ERROR" loading />)
    );
    const workload = screen.getByRole("region", { name: h("workloadTitle") });
    expect(within(workload).getByRole("alert")).toHaveTextContent(h("cachedFailed"));
    expect(within(workload).getByRole("button", { name: h("retry") })).toBeDisabled();
    expect(screen.queryByText("RAW_WORKLOAD")).not.toBeInTheDocument();
  });
  it("renders invalid timestamps and event counts without hydration errors or rewritten diagnostics", async () => {
    const content = wrap(
      locale,
      <Body
        rows={[row({ lastImageDeployedAt: "invalid" })]}
        health={{
          ...HEALTH,
          events: [
            {
              ...HEALTH.events[0],
              reason: "RAW_REASON",
              message: "RAW_MESSAGE",
              involvedObject: "RAW_OBJECT",
              lastSeen: "invalid",
              count: -1,
            },
          ],
        }}
      />
    );
    const container = document.createElement("div");
    container.innerHTML = renderToString(content);
    document.body.appendChild(container);
    const errors: unknown[] = [];
    let root: ReturnType<typeof hydrateRoot>;
    await act(async () => {
      root = hydrateRoot(container, content, { onRecoverableError: (error) => errors.push(error) });
    });
    expect(within(container).getAllByText(h("unknown"))).toHaveLength(3);
    for (const raw of ["RAW_REASON", "RAW_MESSAGE", "RAW_OBJECT"])
      expect(within(container).getByText(raw)).toBeInTheDocument();
    expect(container.textContent).not.toContain("NaN");
    expect(errors).toEqual([]);
    await act(async () => root!.unmount());
    container.remove();
  });
});
