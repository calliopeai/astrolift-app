import type { ReactNode } from "react";

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PromqlQueryPanel } from "./PromqlQueryPanel";

/**
 * The Query panel could always run PromQL. What an operator lacked was any
 * way to know what their app exposes (#1226) — you cannot write a query
 * against a metric whose name you have never seen.
 *
 * These pin the three things that make the picker useful rather than
 * decorative: the names appear, clicking one writes an expression that
 * actually charts, and a truncated list says so.
 */

const state = vi.hoisted(() => ({
  names: ["http_requests_total", "queue_depth"] as string[],
  truncated: false,
  ok: true,
  error: null as string | null,
}));

vi.mock("@apollo/client/react", () => ({
  useQuery: (_doc: unknown, options?: { skip?: boolean }) => ({
    data: options?.skip
      ? undefined
      : {
          astroliftAppMetricNames: {
            ok: state.ok,
            error: state.error,
            names: state.names,
            truncated: state.truncated,
            limit: 200,
          },
        },
    loading: false,
    error: undefined,
  }),
  useLazyQuery: () => [vi.fn(), { data: undefined, loading: false, error: undefined }],
}));

vi.mock("recharts", () => ({
  ResponsiveContainer: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  LineChart: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  Line: () => null,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
  Legend: () => null,
}));

function openPanel() {
  render(<PromqlQueryPanel appSlug="shop" environmentName="production" />);
  // Collapsed by default — discovery is a Prometheus round trip and should
  // not fire for an operator who never opens the panel.
  fireEvent.click(screen.getByRole("button", { name: /query/i }));
}

describe("PromqlQueryPanel metric picker", () => {
  it("lists what the app exposes", () => {
    state.names = ["http_requests_total", "queue_depth"];
    state.ok = true;
    openPanel();

    expect(screen.getByRole("button", { name: "http_requests_total" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "queue_depth" })).toBeInTheDocument();
  });

  it("writes a rate() for a counter rather than the bare name", () => {
    // A counter charted raw is a monotonic ramp, which tells an operator
    // nothing. The box stays editable; this is a better starting point.
    state.names = ["http_requests_total"];
    openPanel();

    fireEvent.click(screen.getByRole("button", { name: "http_requests_total" }));

    expect(screen.getByRole("textbox", { name: "PromQL query" })).toHaveValue(
      "sum(rate(http_requests_total[5m]))"
    );
  });

  it("writes a plain sum for a gauge", () => {
    state.names = ["queue_depth"];
    openPanel();

    fireEvent.click(screen.getByRole("button", { name: "queue_depth" }));

    expect(screen.getByRole("textbox", { name: "PromQL query" })).toHaveValue("sum(queue_depth)");
  });

  it("says so when the list was capped", () => {
    // A capped list looks identical to a rich one, so an app minting a
    // metric name per request id is indistinguishable from a well
    // instrumented one unless this is surfaced.
    state.names = ["a_total"];
    state.truncated = true;
    openPanel();

    expect(screen.getByText(/showing the first 200/i)).toBeInTheDocument();
    state.truncated = false;
  });

  it("tells an uninstrumented app how to opt in", () => {
    state.names = [];
    openPanel();

    expect(screen.getByText(/exposes no metrics of its own/i)).toBeInTheDocument();
    expect(screen.getByText(/\[workloads\.<name>\.metrics\]/)).toBeInTheDocument();
  });

  it("keeps the query box usable when discovery fails", () => {
    // Free-form PromQL does not depend on discovery, so a lookup failure
    // must not take the panel down with it.
    state.names = [];
    state.ok = false;
    state.error = "no Prometheus endpoint configured for this app's cluster";
    openPanel();

    expect(screen.getByText(/can't list this app's metrics/i)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "PromQL query" })).toBeEnabled();
    state.ok = true;
    state.error = null;
  });
});
