import { readFileSync } from "node:fs";
import { parse as parseIcu } from "@formatjs/icu-messageformat-parser";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createTranslator, NextIntlClientProvider, useFormatter } from "next-intl";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ClusterActivityBody, type ClusterActivityBodyProps } from "./ClusterActivityScreen";
import { AUDIT, WORKFLOWS } from "./fixtures";

const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const NOW = new Date("2026-10-01T17:00:00Z");
function content(locale: string, props: ClusterActivityBodyProps) {
  return (
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      now={NOW}
      timeZone="America/Costa_Rica"
    >
      <TooltipProvider>
        <ClusterActivityBody {...props} />
      </TooltipProvider>
    </NextIntlClientProvider>
  );
}
function copy(locale: string) {
  return createTranslator({ locale, messages: catalogs[locale], namespace: "clusterActivity" });
}
const EMPTY: ClusterActivityBodyProps = {
  workflows: { ...WORKFLOWS, runs: [] },
  lifecycle: { ...AUDIT, entries: [] },
};
function run(overrides: Partial<(typeof WORKFLOWS.runs)[number]>) {
  return { ...WORKFLOWS.runs[0], workflowType: "RAW_WORKFLOW_IDENTIFIER", ...overrides };
}

describe.each(locales)("cluster Activity in %s", (locale) => {
  it.each([
    ["RUNNING", "running"],
    ["COMPLETED", "completed"],
    ["FAILED", "failed"],
    ["CANCELED", "canceled"],
    ["TERMINATED", "terminated"],
    ["TIMED_OUT", "timedOut"],
    ["CONTINUED_AS_NEW", "continuedAsNew"],
  ] as const)(
    "translates known %s while retaining its literal token and workflow identity",
    (status, key) => {
      render(content(locale, { ...EMPTY, workflows: { ...WORKFLOWS, runs: [run({ status })] } }));
      expect(screen.getByTitle(status)).toHaveTextContent(copy(locale)(key));
      expect(screen.getByText("RAW_WORKFLOW_IDENTIFIER")).toBeInTheDocument();
      expect(
        screen.getByRole("heading", { name: copy(locale)("workflowTitle") })
      ).toBeInTheDocument();
    }
  );

  it.each(["RAW_FUTURE_STATUS", "__proto__", "constructor", "running", "", undefined, null])(
    "keeps future or malformed status %j neutral without inventing failure",
    (status) => {
      render(
        content(locale, {
          ...EMPTY,
          workflows: { ...WORKFLOWS, runs: [run({ status: status as unknown as string })] },
        })
      );
      const label = typeof status === "string" && status ? status : copy(locale)("unknown");
      const badge = screen.getByText(label).closest('[data-slot="badge"]');
      expect(badge).not.toBeNull();
      expect(badge?.className).not.toContain("bg-destructive");
      expect(screen.queryByText(copy(locale)("failed"))).not.toBeInTheDocument();
    }
  );

  it.each([
    ["2026-10-01T17:00:00Z", "2026-10-01T17:00:00Z", 0],
    ["2026-10-01T17:00:00Z", "2026-10-01T17:00:01Z", 1],
    ["2026-10-01T17:00:00Z", "2026-10-01T17:00:02Z", 2],
  ] as const)(
    "uses plural rules for an observed duration from %s to %s",
    (startedAt, closedAt, count) => {
      render(
        content(locale, {
          ...EMPTY,
          workflows: { ...WORKFLOWS, runs: [run({ startedAt, closedAt })] },
        })
      );
      expect(screen.getByText(`· ${copy(locale)("seconds", { count })}`)).toBeInTheDocument();
    }
  );

  it.each([
    ["2026-10-01T17:00:00Z", "RAW_INVALID_CLOSED_TIME"],
    ["RAW_INVALID_STARTED_TIME", "2026-10-01T17:00:00Z"],
    ["2026-10-01T17:00:01Z", "2026-10-01T17:00:00Z"],
    ["", "2026-10-01T17:00:00Z"],
  ])("keeps invalid/reversed duration %s / %s unknown", (startedAt, closedAt) => {
    const view = render(
      content(locale, {
        ...EMPTY,
        workflows: { ...WORKFLOWS, runs: [run({ startedAt, closedAt })] },
      })
    );
    expect(screen.getByText(`· ${copy(locale)("unknown")}`)).toBeInTheDocument();
    expect(view.container.textContent).not.toContain("NaN");
    expect(
      screen.queryByText(`· ${copy(locale)("seconds", { count: 0 })}`)
    ).not.toBeInTheDocument();
  });

  it("does not invent a closed duration for an open workflow", () => {
    render(
      content(locale, {
        ...EMPTY,
        workflows: { ...WORKFLOWS, runs: [run({ closedAt: "", status: "RUNNING" })] },
      })
    );
    expect(screen.queryByText(`· ${copy(locale)("unknown")}`)).not.toBeInTheDocument();
    expect(
      screen.queryByText(`· ${copy(locale)("seconds", { count: 0 })}`)
    ).not.toBeInTheDocument();
  });

  it("qualifies empty returned feeds without diagnosing Temporal or cluster health", () => {
    render(content(locale, EMPTY));
    expect(screen.getByText(copy(locale)("noRuns"))).toBeInTheDocument();
    expect(screen.getByText(copy(locale)("noEvents"))).toBeInTheDocument();
    expect(screen.getByText(copy(locale)("workflowDescription"))).toBeInTheDocument();
  });

  it("preserves exact audit operation, actor and refusal through localized actor markup", () => {
    render(
      content(locale, {
        ...EMPTY,
        lifecycle: {
          ...AUDIT,
          entries: [
            {
              ...AUDIT.entries[0],
              operation: "RAW_OPERATION",
              actor: "RAW_ACTOR@example.invalid",
              success: false,
              errors: ["RAW_DIAGNOSTIC <literal>"],
            },
          ],
        },
      })
    );
    expect(screen.getByText("RAW_OPERATION")).toBeInTheDocument();
    expect(screen.getByText("RAW_ACTOR@example.invalid")).toHaveClass("font-mono");
    expect(screen.getByText("RAW_DIAGNOSTIC <literal>")).toBeInTheDocument();
  });

  it("retains an audit row with an invalid timestamp under the unknown-date group", () => {
    render(
      content(locale, {
        ...EMPTY,
        lifecycle: {
          ...AUDIT,
          entries: [
            {
              ...AUDIT.entries[0],
              timestamp: "RAW_INVALID_AUDIT_TIME",
              operation: "RAW_AUDIT_OPERATION",
            },
          ],
        },
      })
    );
    expect(screen.getByText(catalogs[locale].shared.feed.unknownDate)).toBeInTheDocument();
    expect(screen.getByText(copy(locale)("unknown"))).toBeInTheDocument();
    expect(screen.getByText("RAW_AUDIT_OPERATION")).toBeInTheDocument();
  });

  it("retries each failed cached feed with its own original callback", async () => {
    const workflows = vi.fn();
    const lifecycle = vi.fn();
    render(
      content(locale, {
        workflows: {
          ...WORKFLOWS,
          runs: [run({})],
          error: "RAW_TEMPORAL_READ",
          refetch: workflows,
        },
        lifecycle: { ...AUDIT, error: "RAW_AUDIT_READ", refetch: lifecycle },
      })
    );
    expect(screen.getByText("RAW_WORKFLOW_IDENTIFIER")).toBeInTheDocument();
    expect(screen.getByText("RAW_TEMPORAL_READ")).toBeInTheDocument();
    expect(screen.getByText("RAW_AUDIT_READ")).toBeInTheDocument();
    const retry = screen.getAllByRole("button", { name: catalogs[locale].shared.feed.retry });
    await userEvent.click(retry[0]);
    expect(workflows).toHaveBeenCalledTimes(1);
    expect(lifecycle).not.toHaveBeenCalled();
    await userEvent.click(retry[1]);
    expect(lifecycle).toHaveBeenCalledTimes(1);
  });

  it("loads older entries through independent feed callbacks", async () => {
    const workflows = vi.fn();
    const lifecycle = vi.fn();
    render(
      content(locale, {
        workflows: {
          ...WORKFLOWS,
          more: { hasMore: true, loadingMore: false, onLoadMore: workflows },
        },
        lifecycle: { ...AUDIT, more: { hasMore: true, loadingMore: false, onLoadMore: lifecycle } },
      })
    );
    const older = screen.getAllByRole("button", { name: catalogs[locale].shared.feed.loadOlder });
    await userEvent.click(older[0]);
    expect(workflows).toHaveBeenCalledTimes(1);
    expect(lifecycle).not.toHaveBeenCalled();
    await userEvent.click(older[1]);
    expect(lifecycle).toHaveBeenCalledTimes(1);
  });

  it("hydrates with request-local date formatting and literal identities intact", async () => {
    const value = content(locale, {
      workflows: {
        ...WORKFLOWS,
        runs: [run({ startedAt: NOW.toISOString(), closedAt: "", status: "RUNNING" })],
      },
      lifecycle: AUDIT,
    });
    const container = document.createElement("div");
    container.innerHTML = renderToString(value);
    document.body.append(container);
    const errors: unknown[] = [];
    let root!: ReturnType<typeof hydrateRoot>;
    await act(async () => {
      root = hydrateRoot(container, value, { onRecoverableError: (error) => errors.push(error) });
    });
    expect(errors).toEqual([]);
    expect(container.textContent).toContain(copy(locale)("running"));
    expect(container.textContent).toContain("RAW_WORKFLOW_IDENTIFIER");
    function DateValue() {
      const fmt = useFormatter();
      return (
        <p>
          {fmt.dateTime(NOW, {
            year: "numeric",
            month: "short",
            day: "numeric",
            hour: "numeric",
            minute: "numeric",
            timeZoneName: "short",
          })}
        </p>
      );
    }
    const date = render(
      <NextIntlClientProvider locale={locale} timeZone="America/Costa_Rica">
        <DateValue />
      </NextIntlClientProvider>
    );
    expect(container.textContent).toContain(date.container.textContent);
    date.unmount();
    await act(async () => root.unmount());
    container.remove();
  });
});

it("keeps Activity ICU arguments and rich actor tags identical across all catalogs", () => {
  function contract(message: string) {
    const values = new Set<string>();
    function walk(nodes: ReturnType<typeof parseIcu>) {
      for (const node of nodes) {
        if (node.type !== 0 && "value" in node) values.add(`${node.type}:${node.value}`);
        if ("children" in node) walk(node.children);
        if ("options" in node) for (const option of Object.values(node.options)) walk(option.value);
      }
    }
    walk(parseIcu(message));
    return [...values].sort();
  }
  const base = catalogs.en.clusterActivity;
  expect(Object.keys(base)).toHaveLength(19);
  for (const locale of locales) {
    expect(Object.keys(catalogs[locale].clusterActivity).sort()).toEqual(Object.keys(base).sort());
    for (const key of Object.keys(base))
      expect(contract(catalogs[locale].clusterActivity[key])).toEqual(contract(base[key]));
  }
});
