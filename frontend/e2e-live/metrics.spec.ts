// #2271: an app's production metrics show at least one traffic series and
// one HTTP status-code series. Network RX/TX bytes are not in the GraphQL
// contract yet, so the traffic signal (HTTP requests/s) is the network
// panel the console renders. An empty panel is reported as 'empty' with the
// GraphQL status and reason.
import { expect, messages, test, type GraphqlCall } from "./live";

const APP = process.env.LIVE_METRICS_APP ?? "conflict-quake-dash";
const ENV = process.env.LIVE_METRICS_ENV ?? "production";

type Samples = { samples?: unknown[] }[];

function panel(call: GraphqlCall | null, series: Samples, reason: unknown) {
  const populated = series.filter((s) => (s.samples ?? []).length > 0).length;
  return {
    result: populated > 0 ? "populated" : "empty",
    series: populated,
    reason: reason ?? null,
    graphqlStatus: call?.status ?? "no response",
    graphqlErrors: call?.errors ?? null,
  };
}

test("production metrics show traffic and status-code series", async ({ page, live }) => {
  await page.goto(`/apps/${APP}/logs?section=metrics&env=${encodeURIComponent(ENV)}`);
  await expect(
    page.getByRole("heading", { name: messages.obsGoldenSignals.title, exact: true })
  ).toBeVisible();
  const signalsCall = await live.latest("GetAppGoldenSignals");
  const statusCall = await live.latest("GetAppStatusCodeBreakdown");
  const signals = signalsCall?.data?.astroliftAppGoldenSignals as
    | { reason?: string; signals?: ({ name: string; reason?: string } & Samples[number])[] }
    | undefined;
  const traffic = signals?.signals?.find((s) => s.name === "TRAFFIC");
  const breakdown = statusCall?.data?.astroliftAppStatusCodeBreakdown as
    | { reason?: string; series?: Samples }
    | undefined;
  const network = panel(signalsCall, traffic ? [traffic] : [], traffic?.reason ?? signals?.reason);
  const statusCodes = panel(statusCall, breakdown?.series ?? [], breakdown?.reason);
  live.report.metrics = { app: APP, environment: ENV, network, statusCodes };
  await live.screenshot("metrics");
  expect
    .soft(network.result, `network (traffic) panel: ${JSON.stringify(network)}`)
    .toBe("populated");
  expect
    .soft(statusCodes.result, `status-code panel: ${JSON.stringify(statusCodes)}`)
    .toBe("populated");
});
