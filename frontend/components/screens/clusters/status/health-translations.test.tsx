import { readFileSync } from "node:fs";
import { parse as parseIcu } from "@formatjs/icu-messageformat-parser";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createTranslator, NextIntlClientProvider, useFormatter } from "next-intl";
import type { ReactNode } from "react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { TooltipProvider } from "@/components/ui/tooltip";
import { StatusLiveHealthCard, StatusWorkloadHealthCard } from "./ClusterStatusScreen";
import { HEALTH, WORKLOADS } from "./fixtures";
import type { PodPhase, WorkloadRow } from "./types";

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
function copy(locale: string) {
  return createTranslator({ locale, messages: catalogs[locale], namespace: "clusterHealth" });
}
function workload(overrides: Partial<WorkloadRow> = {}): WorkloadRow {
  return {
    ...WORKLOADS.rows[0],
    workloadName: "RAW_WORKLOAD_NAME",
    namespace: "RAW_NAMESPACE",
    readyReplicas: 2,
    desiredReplicas: 3,
    restartCount24h: 0,
    lastImageDeployedAt: "2026-10-01T16:55:00Z",
    ...overrides,
  };
}

describe.each(locales)("cluster Status health in %s", (locale) => {
  const t = copy(locale);

  it("has a complete translated health catalog with valid ICU syntax", () => {
    expect(Object.keys(catalogs[locale].clusterHealth).sort()).toEqual(
      Object.keys(catalogs.en.clusterHealth).sort()
    );
    expect(Object.keys(catalogs[locale].clusterHealth)).toHaveLength(24);
    for (const value of Object.values(catalogs[locale].clusterHealth)) {
      expect(typeof value).toBe("string");
      expect(() => parseIcu(value as string)).not.toThrow();
    }
    if (locale !== "en") {
      for (const key of [
        "workloadTitle",
        "workloadHelp",
        "liveTitle",
        "liveHelp",
        "cachedFailed",
      ]) {
        expect(catalogs[locale].clusterHealth[key]).not.toEqual(catalogs.en.clusterHealth[key]);
      }
    }
  });

  it.each([1, 2, 1000])("uses locale plural and number rules for %s restarts", (count) => {
    render(
      wrap(
        locale,
        <StatusWorkloadHealthCard
          slug="RAW_SLUG"
          {...WORKLOADS}
          rows={[workload({ restartCount24h: count })]}
        />
      )
    );
    expect(
      screen.getByText(
        (_, element) =>
          element?.getAttribute("data-slot") === "badge" &&
          element.textContent === t("restarts", { count })
      )
    ).toBeInTheDocument();
    expect(screen.getByTitle("RAW_NAMESPACE/RAW_WORKLOAD_NAME")).toHaveTextContent(
      "RAW_WORKLOAD_NAME"
    );
    expect(screen.getByRole("heading", { name: t("workloadTitle") })).toBeInTheDocument();
    expect(screen.getByText(t("workloadHelp"))).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("href", "/clusters/RAW_SLUG/health");
  });

  it.each([
    ["Running", "running"],
    ["Succeeded", "succeeded"],
    ["Pending", "pending"],
    ["Failed", "failed"],
    ["CrashLoopBackOff", "crashLoop"],
    ["Unknown", "phaseUnknown"],
  ] as const)("translates known phase %s and retains its literal token", (phase, key) => {
    render(
      wrap(
        locale,
        <StatusLiveHealthCard
          {...HEALTH}
          pods={[{ phase, namespace: "RAW_NAMESPACE", count: 1 }]}
          events={[]}
        />
      )
    );
    expect(screen.getByTitle(phase)).toHaveTextContent(t(key));
    expect(screen.getByRole("heading", { name: t("liveTitle") })).toBeInTheDocument();
    expect(screen.getByText(t("podPhases"))).toBeInTheDocument();
    expect(screen.getByText(t("recentEvents"))).toBeInTheDocument();
    expect(screen.getByText(t("noEvents"))).toBeInTheDocument();
  });

  it.each(["RAW_FUTURE_PHASE", "__proto__", "constructor", "running", ""])(
    "keeps unknown phase %j neutral without rewriting it",
    (phase) => {
      render(
        wrap(
          locale,
          <StatusLiveHealthCard
            {...HEALTH}
            pods={[{ phase, namespace: "RAW_NAMESPACE", count: 1 }]}
            events={[]}
          />
        )
      );
      const pill = screen.getByText(phase || t("unknown")).parentElement!;
      expect(pill).toHaveAttribute("title", phase);
      expect(pill.className).toContain("text-muted-foreground");
      expect(pill.className).not.toContain("text-success-fg");
      expect(pill.className).not.toContain("text-destructive");
    }
  );

  it.each([-1, 0.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1])(
    "does not invent healthy readiness or no restarts from malformed count %s",
    (value) => {
      render(
        wrap(
          locale,
          <StatusWorkloadHealthCard
            slug="RAW_SLUG"
            {...WORKLOADS}
            rows={[workload({ readyReplicas: value, restartCount24h: value })]}
          />
        )
      );
      expect(screen.getAllByText(t("unknown"))).toHaveLength(2);
      expect(screen.queryByText(t("noRestarts"))).not.toBeInTheDocument();
      for (const unknown of screen.getAllByText(t("unknown"))) {
        expect(unknown.className).toContain("text-muted-foreground");
      }
    }
  );

  it("does not show ready replicas exceeding the desired count as healthy", () => {
    render(
      wrap(
        locale,
        <StatusWorkloadHealthCard
          slug="RAW_SLUG"
          {...WORKLOADS}
          rows={[workload({ readyReplicas: 4 })]}
        />
      )
    );
    expect(screen.getByText(t("unknown"))).toHaveClass("text-muted-foreground");
  });

  it("retains reported zero counts with a neutral zero-sized Deployment", () => {
    render(
      wrap(
        locale,
        <StatusWorkloadHealthCard
          slug="RAW_SLUG"
          {...WORKLOADS}
          rows={[workload({ readyReplicas: 0, desiredReplicas: 0 })]}
        />
      )
    );
    expect(screen.getByText("0 / 0")).toHaveClass("text-muted-foreground");
    expect(screen.getByText(t("noRestarts"))).toBeInTheDocument();
    expect(screen.queryByText(t("unknown"))).not.toBeInTheDocument();
  });

  it.each([
    [2, 3, 5],
    [0, 0, 0],
    [2, -1, null],
    [-1, 2, null],
    [2, NaN, null],
    [2, 0.5, null],
    [Number.MAX_SAFE_INTEGER, 1, null],
  ] as const)(
    "aggregates observed phase counts %s and %s without fabricating totals",
    (a, b, total) => {
      const pods: PodPhase[] = [
        { phase: "Running", namespace: "RAW_A", count: a },
        { phase: "Running", namespace: "RAW_B", count: b },
      ];
      render(wrap(locale, <StatusLiveHealthCard {...HEALTH} pods={pods} events={[]} />));
      const pill = screen.getByTitle("Running");
      const count = pill.querySelector(".tabular-nums")!;
      expect(count).toHaveTextContent(total === null ? t("unknown") : String(total));
      if (total === null) expect(pill).toHaveClass("text-muted-foreground");
    }
  );

  it("qualifies empty successful driver reports without diagnosing an unreachable apiserver", () => {
    render(
      wrap(
        locale,
        <>
          <StatusLiveHealthCard {...HEALTH} pods={[]} events={[]} />
          <StatusWorkloadHealthCard slug="RAW_SLUG" {...WORKLOADS} rows={[]} />
        </>
      )
    );
    expect(screen.getByText(t("noHealthHelp"))).toBeInTheDocument();
    expect(screen.getByText(t("noDeploymentsHelp"))).toBeInTheDocument();
    expect(screen.queryByText("The driver couldn't reach the apiserver.")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("retains cached reports, exposes raw failed-read diagnostics and retries each read independently", async () => {
    const health = vi.fn();
    const workloads = vi.fn();
    render(
      wrap(
        locale,
        <>
          <StatusLiveHealthCard {...HEALTH} error="RAW_HEALTH_READ_DIAGNOSTIC" refetch={health} />
          <StatusWorkloadHealthCard
            slug="RAW_SLUG"
            {...WORKLOADS}
            rows={[workload()]}
            error="RAW_WORKLOAD_READ_DIAGNOSTIC"
            refetch={workloads}
          />
        </>
      )
    );
    const healthPanel = screen.getByRole("region", { name: t("liveTitle") });
    const workloadPanel = screen.getByRole("region", { name: t("workloadTitle") });
    expect(within(healthPanel).getByRole("alert")).toHaveTextContent(t("cachedFailed"));
    expect(within(healthPanel).getByText("RAW_HEALTH_READ_DIAGNOSTIC")).toBeInTheDocument();
    expect(within(workloadPanel).getByText("RAW_WORKLOAD_NAME")).toBeInTheDocument();
    expect(within(workloadPanel).getByRole("alert")).toHaveTextContent(
      "RAW_WORKLOAD_READ_DIAGNOSTIC"
    );
    expect(health).not.toHaveBeenCalled();
    expect(workloads).not.toHaveBeenCalled();
    const user = userEvent.setup();
    await user.click(within(healthPanel).getByRole("button", { name: t("retry") }));
    expect(health).toHaveBeenCalledTimes(1);
    expect(workloads).not.toHaveBeenCalled();
    await user.click(within(workloadPanel).getByRole("button", { name: t("retry") }));
    expect(health).toHaveBeenCalledTimes(1);
    expect(workloads).toHaveBeenCalledTimes(1);
  });

  it("labels pending cached refreshes and disables failed-read retries while refreshing", () => {
    render(
      wrap(
        locale,
        <>
          <StatusLiveHealthCard {...HEALTH} loading />
          <StatusWorkloadHealthCard
            slug="RAW_SLUG"
            {...WORKLOADS}
            loading
            error="RAW_RETRY_PENDING"
          />
        </>
      )
    );
    expect(screen.getByRole("status")).toHaveTextContent(t("cachedPending"));
    expect(screen.getByRole("alert")).toHaveTextContent(t("cachedFailed"));
    expect(screen.getByRole("button", { name: t("retry") })).toBeDisabled();
  });

  it("shows failed initial reads with retry and no successful empty-state diagnosis", () => {
    render(
      wrap(
        locale,
        <StatusLiveHealthCard {...HEALTH} pods={[]} events={[]} error="RAW_INITIAL_HEALTH_ERROR" />
      )
    );
    expect(screen.getByText("RAW_INITIAL_HEALTH_ERROR")).toBeInTheDocument();
    expect(screen.getByRole("button")).toBeInTheDocument();
    expect(screen.queryByText(t("noHealthHelp"))).not.toBeInTheDocument();
  });

  it("formats event ages against the request clock and preserves provider diagnostics literally", () => {
    function ExpectedAge() {
      return <output>{useFormatter().relativeTime(new Date("2026-10-01T16:55:00Z"), NOW)}</output>;
    }
    render(
      wrap(
        locale,
        <>
          <ExpectedAge />
          <StatusLiveHealthCard
            {...HEALTH}
            pods={[]}
            events={[
              {
                ...HEALTH.events[0],
                reason: "RAW_PROVIDER_REASON",
                message: "RAW_PROVIDER_MESSAGE",
                involvedObject: "RAW_INVOLVED_OBJECT",
                lastSeen: "2026-10-01T16:55:00Z",
              },
            ]}
          />
        </>
      )
    );
    const expected = screen.getByRole("status").textContent!;
    expect(screen.getAllByText(expected)).toHaveLength(2);
    for (const raw of ["RAW_PROVIDER_REASON", "RAW_PROVIDER_MESSAGE", "RAW_INVOLVED_OBJECT"])
      expect(screen.getByText(raw)).toBeInTheDocument();
  });

  it("keeps invalid event and image dates unknown through hydration", async () => {
    const content = wrap(
      locale,
      <>
        <StatusLiveHealthCard
          {...HEALTH}
          pods={[]}
          events={[{ ...HEALTH.events[0], lastSeen: "RAW_INVALID_TIME" }]}
        />
        <StatusWorkloadHealthCard
          slug="RAW_SLUG"
          {...WORKLOADS}
          rows={[workload({ lastImageDeployedAt: "RAW_INVALID_TIME" })]}
        />
      </>
    );
    const container = document.createElement("div");
    container.innerHTML = renderToString(content);
    document.body.appendChild(container);
    const errors: unknown[] = [];
    let root: ReturnType<typeof hydrateRoot>;
    await act(async () => {
      root = hydrateRoot(container, content, { onRecoverableError: (error) => errors.push(error) });
    });
    expect(within(container).getAllByText(t("unknown"))).toHaveLength(2);
    expect(container.textContent).not.toContain("NaN");
    expect(errors).toEqual([]);
    await act(async () => root!.unmount());
    container.remove();
  });
});
