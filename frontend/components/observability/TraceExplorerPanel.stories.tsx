import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";
import * as React from "react";

import {
  TraceExplorerPanel,
  type TraceStatusFilter,
} from "@/components/observability/TraceExplorerPanel";
import type { AstroliftAppTrace, AstroliftTraceSpan } from "@/graphql/__generated__/schema";

const meta: Meta = { title: "Patterns/Observability/TraceExplorerPanel" };
export default meta;

const trace = (
  traceId: string,
  rootOperation: string,
  durationMs: number,
  statusCode: string
): AstroliftAppTrace =>
  ({
    traceId,
    rootService: "checkout",
    rootOperation,
    durationMs,
    spanCount: 3,
    statusCode,
  }) as AstroliftAppTrace;

const span = (
  spanId: string,
  parentSpanId: string | null,
  service: string,
  operation: string,
  durationMs: number
): AstroliftTraceSpan =>
  ({
    traceId: "t1",
    spanId,
    parentSpanId,
    service,
    operation,
    durationMs,
    statusCode: "OK",
    startTime: "2026-09-28T12:00:00Z",
    attributes: {},
  }) as AstroliftTraceSpan;

const TRACES = [
  trace("t1", "POST /api/checkout", 480, "OK"),
  trace("t2", "GET /api/cart", 38, "ERROR"),
];
const SPANS = {
  t1: {
    loading: false,
    spans: [
      span("s1", null, "checkout", "POST /api/checkout", 480),
      span("s2", "s1", "payments", "charge", 310),
      span("s3", "s1", "postgres", "INSERT orders", 22),
    ],
  },
};

function Demo() {
  const [filter, setFilter] = React.useState<TraceStatusFilter>("ALL");
  return (
    <TraceExplorerPanel
      traces={TRACES}
      loading={false}
      statusFilter={filter}
      onStatusFilterChange={setFilter}
      spans={SPANS}
      onExpand={() => {}}
    />
  );
}

export const Traces: StoryObj = { render: () => <Demo /> };
/** The first trace opened to its spans. */
export const Expanded: StoryObj = {
  render: () => <Demo />,
  play: async ({ canvasElement }) => {
    await userEvent.click(within(canvasElement).getByText("POST /api/checkout"));
    await expect(within(canvasElement).getByText("charge")).toBeTruthy();
  },
};
export const Loading: StoryObj = {
  render: () => (
    <TraceExplorerPanel
      traces={[]}
      loading
      statusFilter="ALL"
      onStatusFilterChange={() => {}}
      spans={{}}
      onExpand={() => {}}
    />
  ),
};
