import { readFileSync } from "node:fs";
import path from "node:path";
import {
  ApolloClient,
  ApolloLink,
  InMemoryCache,
  Observable,
  type Operation,
} from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import {
  TRIGGERED_BY_ME,
  TRIGGERED_BY_OTHER,
} from "@/components/screens/deployments/deployments.fixtures";
import { HOME_PANELS } from "../registry";
import { AgentRunsPanelView } from "./AgentRunsPanel";
import { AlertsPanelView } from "./AlertsPanel";
import { ClustersPanelView } from "./ClustersPanel";
import { MyAgentsPanelView } from "./MyAgentsPanel";
import { MyAppsPanelView } from "./MyAppsPanel";
import { RecentDeploymentsPanelView } from "./RecentDeploymentsPanel";
import { RunsSpendPanelView } from "./RunsSpendPanel";
import { SpendQuotaPanelView } from "./SpendQuotaPanel";
import { TrafficErrorsPanelView } from "./TrafficErrorsPanel";
import { WaitingPanelView } from "./WaitingPanel";
import {
  MY_APPS,
  MY_AGENTS,
  RECENT_DEPLOYS,
  RUN_DAYS,
  WAITING,
  TRAFFIC,
  ERRORS,
  APPS_PICK,
  SPEND,
  READY,
} from "./apps-agents.fixtures";
import {
  ALERTS,
  CLUSTERS,
  BUDGET,
  FORECAST,
  METRICS,
  AGENT_RUNS,
} from "./builder-operator.fixtures";
import { useHomePresentation } from "./use-home-presentation";
import { useKpisPanel } from "./use-home-panels";
import { useWaiting, deployItem, gateItem, secretItem } from "./use-waiting";
import { failedDeployItem } from "./use-failing";
import { agentFailedItem } from "./use-failed-runs";
import { platformRunReason } from "./builder-operator-model";
import type { PendingHumanGate } from "@/graphql/workflows/tiered.types";
import type { AstroliftSecretChangeProposal } from "@/graphql/services/services.types";

const authority = vi.hoisted(() => ({
  modules: new Set(["apps"]),
  permissions: new Set(["app.read", "app.approve_deploy", "billing.read"]),
  success: vi.fn(),
  error: vi.fn(),
}));
vi.mock("@/graphql/user/user.hooks", () => ({
  useModules: () => ({ canView: (key: string) => authority.modules.has(key) }),
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: (key: string) => authority.permissions.has(key) }),
}));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: "actual-org" } }),
}));
vi.mock("sonner", () => ({ toast: { success: authority.success, error: authority.error } }));
const catalogs = Object.fromEntries(
  locales.map((locale) => [
    locale,
    JSON.parse(readFileSync(path.resolve("messages", `${locale}.json`), "utf8")),
  ])
);
const now = new Date("2026-09-28T14:00:00Z");
function wrapper(locale: string, client?: ApolloClient) {
  const onError = vi.fn();
  return {
    onError,
    wrap: ({ children }: { children: ReactNode }) => (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        timeZone="America/New_York"
        now={now}
        onError={onError}
      >
        {client ? <ApolloProvider client={client}>{children}</ApolloProvider> : children}
      </NextIntlClientProvider>
    ),
  };
}
const translator = (locale: string) =>
  createTranslator({ locale, messages: catalogs[locale], namespace: "home" });
beforeEach(() => {
  authority.modules = new Set(["apps"]);
  authority.permissions = new Set(["app.read", "app.approve_deploy", "billing.read"]);
  authority.success.mockClear();
  authority.error.mockClear();
});
describe("Home panel locale behavior", () => {
  it("updates observed relative ages without another KPI network read", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    const requests: string[] = [];
    authority.permissions = new Set(["app.read"]);
    const client = new ApolloClient({
      cache: new InMemoryCache(),
      link: new ApolloLink(
        (operation) =>
          new Observable((observer) => {
            requests.push(operation.operationName ?? "");
            observer.next({ data: { astroliftDeploymentMetrics: METRICS } });
            observer.complete();
          })
      ),
    });
    const w = wrapper("fr", client);
    const hook = renderHook(() => ({ kpi: useKpisPanel(), clock: useHomePresentation(true) }), {
      wrapper: w.wrap,
    });
    try {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(hook.result.current.kpi.figures[0].value).toBe("14");
      expect(hook.result.current.clock.age("2026-09-28T13:30:00Z")).toBe(
        new Intl.RelativeTimeFormat("fr", { numeric: "auto" }).format(-30, "minute")
      );
      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000);
      });
      expect(hook.result.current.clock.age("2026-09-28T13:30:00Z")).toBe(
        new Intl.RelativeTimeFormat("fr", { numeric: "auto" }).format(-31, "minute")
      );
      expect(requests).toEqual(["GetDeploymentMetrics"]);
      expect(w.onError).not.toHaveBeenCalled();
    } finally {
      hook.unmount();
      client.stop();
      vi.useRealTimers();
    }
  });
  it.each(locales)(
    "%s confirms actual deploy targets and retains original refusal for retry",
    async (locale) => {
      const t = translator(locale),
        approve = vi
          .fn()
          .mockRejectedValueOnce(new Error("ORIGINAL_APPROVAL_REFUSAL"))
          .mockResolvedValueOnce(undefined),
        w = wrapper(locale);
      render(
        <WaitingPanelView
          panel={HOME_PANELS.waiting}
          items={WAITING}
          approving={false}
          onApprove={approve}
          {...READY}
        />,
        { wrapper: w.wrap }
      );
      expect(screen.getByRole("link", { name: WAITING[0].title })).toHaveAttribute(
        "href",
        WAITING[0].href
      );
      fireEvent.click(screen.getByRole("button", { name: t("copy.approve") }));
      const dialog = await screen.findByRole("alertdialog");
      expect(dialog).toHaveTextContent(WAITING[0].detail);
      fireEvent.click(within(dialog).getByRole("button", { name: t("copy.approve") }));
      await waitFor(() =>
        expect(authority.error).toHaveBeenCalledExactlyOnceWith("ORIGINAL_APPROVAL_REFUSAL")
      );
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
      expect(approve).toHaveBeenCalledExactlyOnceWith(WAITING[0].approveId);
      fireEvent.click(within(dialog).getByRole("button", { name: t("copy.approve") }));
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(approve).toHaveBeenCalledTimes(2);
      expect(w.onError).not.toHaveBeenCalled();
    }
  );
  it.each(locales)(
    "%s localizes observed statuses and counts while preserving names, IDs and routes",
    (locale) => {
      const t = translator(locale),
        w = wrapper(locale);
      render(
        <>
          <MyAppsPanelView
            panel={HOME_PANELS["my-apps"]}
            items={MY_APPS}
            count={1234}
            mineNote="EXACT_CALLER_NOTE"
            {...READY}
          />
          <MyAgentsPanelView
            panel={HOME_PANELS["my-agents"]}
            items={MY_AGENTS}
            count={MY_AGENTS.length}
            {...READY}
          />
          <RecentDeploymentsPanelView
            panel={HOME_PANELS["recent-deployments"]}
            items={[{ ...RECENT_DEPLOYS[0], status: "FUTURE_RAW_STATUS" }]}
            count={null}
            {...READY}
          />
          <AlertsPanelView panel={HOME_PANELS.alerts} rows={ALERTS} count={2} {...READY} />
          <AgentRunsPanelView
            panel={HOME_PANELS["agent-runs"]}
            rows={[
              { ...AGENT_RUNS[0], status: "completed" },
              { ...AGENT_RUNS[1], status: "FUTURE_RAW_RUN_STATUS" },
            ]}
            count={12345}
            {...READY}
          />
          <ClustersPanelView
            panel={HOME_PANELS.clusters}
            rows={CLUSTERS}
            count={CLUSTERS.length}
            {...READY}
          />
        </>,
        { wrapper: w.wrap }
      );
      expect(screen.getByText("EXACT_CALLER_NOTE")).toBeInTheDocument();
      expect(screen.getByText("FUTURE_RAW_STATUS")).toBeInTheDocument();
      expect(screen.getByTitle("FUTURE_RAW_RUN_STATUS")).toHaveTextContent("FUTURE_RAW_RUN_STATUS");
      expect(screen.getByText(t("status.unknown"))).toBeInTheDocument();
      expect(screen.getByText(t("status.succeeded"))).toBeInTheDocument();
      expect(
        within(screen.getByRole("region", { name: t("panels.my-apps") })).getAllByText(
          new Intl.NumberFormat(locale).format(1234),
          { normalizer: (value) => value }
        ).length
      ).toBeGreaterThan(0);
      expect(screen.getByRole("link", { name: new RegExp(MY_APPS[0].name) })).toHaveAttribute(
        "href",
        `/apps/${encodeURIComponent(MY_APPS[0].slug)}`
      );
      expect(screen.getByText(t("copy.neverDeployed"))).toBeInTheDocument();
      expect(
        screen.getByText(t("copy.runningCount", { count: MY_AGENTS[0].runningCount }))
      ).toBeInTheDocument();
      for (const cluster of CLUSTERS.slice(0, 5))
        expect(screen.getByRole("link", { name: new RegExp(cluster.name) })).toHaveAttribute(
          "href",
          `/clusters/${encodeURIComponent(cluster.slug)}`
        );
      expect(w.onError).not.toHaveBeenCalled();
    }
  );
  it.each(locales)(
    "%s formats genuine money, budget meter values, rates and missing observations",
    (locale) => {
      const t = translator(locale),
        w = wrapper(locale);
      const view = render(
        <SpendQuotaPanelView
          panel={HOME_PANELS["spend-quota"]}
          forecast={FORECAST}
          budget={BUDGET}
          {...READY}
        />,
        { wrapper: w.wrap }
      );
      const money = (c: number, currency: string) =>
        new Intl.NumberFormat(locale, {
          style: "currency",
          currency,
          maximumFractionDigits: 0,
        }).format(c / 100);
      const meter = screen.getByRole("meter");
      expect(meter).toHaveAttribute("aria-valuenow", String(FORECAST.mtdCents / 100));
      expect(meter).toHaveAttribute("aria-valuemax", String(BUDGET.amountCents / 100));
      expect(meter).toHaveAttribute(
        "aria-valuetext",
        t("copy.spendBudgetValue", {
          spent: money(FORECAST.mtdCents, FORECAST.currency),
          budget: money(BUDGET.amountCents, BUDGET.currency),
        })
      );
      view.rerender(
        <SpendQuotaPanelView
          panel={HOME_PANELS["spend-quota"]}
          forecast={null}
          budget={null}
          {...READY}
        />
      );
      expect(screen.getByText("—")).toBeInTheDocument();
      expect(screen.queryByRole("meter")).not.toBeInTheDocument();
      expect(screen.queryByText(money(0, "USD"))).not.toBeInTheDocument();
      view.unmount();
      const chart = render(
        <RunsSpendPanelView
          panel={HOME_PANELS["runs-spend"]}
          days={RUN_DAYS}
          capped
          spend={SPEND}
          {...READY}
        />,
        { wrapper: w.wrap }
      );
      const count = RUN_DAYS.reduce((sum, d) => sum + d.runs, 0);
      expect(
        screen.getByText(`${new Intl.NumberFormat(locale).format(count)}+`)
      ).toBeInTheDocument();
      expect(screen.getByText(t("copy.cappedRuns"))).toBeInTheDocument();
      chart.unmount();
      render(
        <TrafficErrorsPanelView
          panel={HOME_PANELS["traffic-errors"]}
          apps={APPS_PICK}
          appSlug={APPS_PICK[0].slug}
          onAppChange={vi.fn()}
          traffic={TRAFFIC}
          errors={ERRORS}
          reason="OK"
          {...READY}
        />,
        { wrapper: w.wrap }
      );
      const sample = TRAFFIC.values.at(-1)!;
      expect(
        screen.getByText(
          t("operations.requestRate", {
            value: new Intl.NumberFormat(locale, {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            }).format(sample),
          })
        )
      ).toBeInTheDocument();
      expect(w.onError).not.toHaveBeenCalled();
    }
  );
  it.each(locales)("%s distinguishes every metric reason and actual read retries", (locale) => {
    const t = translator(locale),
      retry = vi.fn(),
      change = vi.fn(),
      w = wrapper(locale);
    const props = {
      panel: HOME_PANELS["traffic-errors"],
      apps: APPS_PICK,
      appSlug: APPS_PICK[0].slug,
      onAppChange: change,
      traffic: null,
      errors: null,
      ...READY,
      onRetry: retry,
    };
    const view = render(<TrafficErrorsPanelView {...props} reason="NOT_CONFIGURED" />, {
      wrapper: w.wrap,
    });
    for (const [reason, key] of [
      ["NOT_CONFIGURED", "notConfiguredTitle"],
      ["NOT_SUPPORTED_BY_PROVIDER", "notSupportedTitle"],
      ["ERROR", "metricErrorTitle"],
      ["NO_DATA_YET", "noRequestsTitle"],
    ] as const) {
      view.rerender(<TrafficErrorsPanelView {...props} reason={reason} />);
      expect(screen.getByText(t(`copy.${key}`))).toBeInTheDocument();
    }
    view.rerender(
      <TrafficErrorsPanelView {...props} reason="ERROR" error="ORIGINAL_METRIC_REFUSAL" />
    );
    expect(screen.getByText("ORIGINAL_METRIC_REFUSAL")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button"));
    expect(retry).toHaveBeenCalledOnce();
    expect(change).not.toHaveBeenCalled();
    expect(w.onError).not.toHaveBeenCalled();
  });
  it.each(locales)(
    "%s translates generated fallback sentences while preserving source diagnostics",
    (locale) => {
      const t = translator(locale),
        deployment = {
          ...TRIGGERED_BY_OTHER,
          environmentName: "",
          statusReason: "",
          abortedReason: "",
          buildError: "",
        };
      expect(deployItem(deployment, t)).toMatchObject({
        approveId: deployment.id,
        title: t("operations.deployTitle", {
          app: deployment.registeredAppSlug,
          environment: t("operations.everyEnvironment"),
        }),
      });
      expect(failedDeployItem(deployment, t).reason).toBe(t("copy.noFailureReason"));
      expect(
        failedDeployItem({ ...deployment, statusReason: "EXACT_SERVER_REASON\nserver detail" }, t)
          .reason
      ).toBe("EXACT_SERVER_REASON");
      expect(
        agentFailedItem({ ...AGENT_RUNS[0], failureMessage: null }, t("copy.noRunReason")).reason
      ).toBe(t("copy.noRunReason"));
      expect(
        agentFailedItem(
          { ...AGENT_RUNS[0], failureMessage: "EXACT_RUN_REASON" },
          t("copy.noRunReason")
        ).reason
      ).toBe("EXACT_RUN_REASON");
      const gate: PendingHumanGate = {
        executionId: "actual-stage",
        runGuid: "actual-run",
        workflowId: "actual-workflow",
        definitionSlug: "actual-definition",
        definitionName: "Actual definition",
        stageRole: "",
        stageApprovers: [],
        startedAt: now.toISOString(),
      };
      expect(gateItem(gate, t)).toMatchObject({
        key: "gate:actual-run:actual-stage",
        detail: t("operations.anyApprover"),
        href: "/workflows/actual-definition/runs?run=actual-run",
      });
      expect(gateItem({ ...gate, stageApprovers: ["ACTUAL_APPROVER"] }, t).detail).toBe(
        t("operations.approvers", { approvers: "ACTUAL_APPROVER" })
      );
      const proposal: AstroliftSecretChangeProposal = {
        id: "actual-proposal",
        registeredAppSlug: "actual-app",
        environmentName: "",
        op: "ACTUAL_RAW_OPERATION",
        status: "pending",
        proposerUserId: "actual-user",
        proposerDisplayName: "Actual User",
        requiredApproverCount: 2000,
        approvalsCount: 1234,
        expiresAt: now.toISOString(),
        decidedAt: null,
        appliedAt: null,
        applyError: "",
        createdAt: now.toISOString(),
        payload: {},
        payloadDiff: {},
        approvals: [],
      };
      expect(secretItem(proposal, t)).toMatchObject({
        href: "/approvals/secret/actual-proposal",
        title: t("operations.secretTitle", {
          app: "actual-app",
          environment: t("operations.appWide"),
        }),
        detail: t("operations.secretDetail", {
          operation: t("operations.secretBy", {
            operation: "ACTUAL_RAW_OPERATION",
            name: "Actual User",
          }),
          received: 1234,
          required: 2000,
        }),
      });
      expect(
        platformRunReason(
          { status: "failed", failure: { message: "EXACT_PLATFORM_DIAGNOSTIC" } },
          { timeout: t("copy.timedOut"), missing: t("copy.noReason") }
        )
      ).toBe("EXACT_PLATFORM_DIAGNOSTIC");
    }
  );
  it.each(locales)(
    "%s preserves KPI and approval network contracts and denied sources",
    async (locale) => {
      const requests: Operation[] = [],
        t = translator(locale);
      let approvalOk = false;
      const deployment = {
        ...TRIGGERED_BY_OTHER,
        registeredAppSlug: "actual-app",
        environmentName: "actual-prod",
        imageTag: "actual-image",
        approvalsReceived: 1234,
        approvalsRequired: 2000,
      };
      const client = new ApolloClient({
        cache: new InMemoryCache(),
        link: new ApolloLink(
          (operation) =>
            new Observable((observer) => {
              requests.push(operation);
              const data =
                operation.operationName === "GetDeploymentMetrics"
                  ? { astroliftDeploymentMetrics: { ...METRICS, total: 1234 } }
                  : operation.operationName === "GetCostForecast"
                    ? { astroliftCostForecast: FORECAST }
                    : operation.operationName === "ListDeployments"
                      ? { astroliftDeployments: [deployment, TRIGGERED_BY_ME] }
                      : operation.operationName === "ApproveDeployment"
                        ? {
                            approveDeployment: {
                              ok: approvalOk,
                              errors: approvalOk
                                ? []
                                : [{ code: "DENIED", message: "RAW_POLICY_REFUSAL" }],
                              data: approvalOk ? deployment : null,
                            },
                          }
                        : {};
              observer.next({ data });
              observer.complete();
            })
        ),
      });
      const w = wrapper(locale, client),
        hook = renderHook(
          () => ({
            kpi: useKpisPanel(),
            waiting: useWaiting(),
            presentation: useHomePresentation(),
          }),
          { wrapper: w.wrap }
        );
      await waitFor(() =>
        expect(hook.result.current.kpi.figures.find((f) => f.key === "deploys")?.value).toBe(
          new Intl.NumberFormat(locale).format(1234)
        )
      );
      await waitFor(() => expect(hook.result.current.waiting.items).toHaveLength(1));
      expect(hook.result.current.waiting.items[0]).toMatchObject({
        approveId: deployment.id,
        href: `/deployments/${deployment.id}`,
        title: t("operations.deployTitle", { app: "actual-app", environment: "actual-prod" }),
        detail: t("operations.deployDetail", {
          image: "actual-image",
          received: 1234,
          required: 2000,
        }),
      });
      expect(hook.result.current.kpi.figures.map((f) => f.key)).toEqual([
        "deploys",
        "success",
        "p95",
        "spend",
      ]);
      expect(hook.result.current.kpi.figures.find((f) => f.key === "spend")?.value).toBe(
        new Intl.NumberFormat(locale, {
          style: "currency",
          currency: "USD",
          maximumFractionDigits: 0,
        }).format(FORECAST.mtdCents / 100)
      );
      expect(hook.result.current.presentation.age("2026-09-28T13:30:00Z")).toBe(
        new Intl.RelativeTimeFormat(locale, { numeric: "auto" }).format(-30, "minute")
      );
      expect(hook.result.current.presentation.age("invalid-date")).toBe(t("copy.unknownTime"));
      await act(async () => {
        await expect(hook.result.current.waiting.onApprove(deployment.id)).rejects.toThrow(
          "RAW_POLICY_REFUSAL"
        );
      });
      expect(authority.success).not.toHaveBeenCalled();
      approvalOk = true;
      await act(async () => {
        await hook.result.current.waiting.onApprove(deployment.id);
      });
      expect(authority.success).toHaveBeenCalledExactlyOnceWith(
        t("operations.approved", { status: t(`status.${deployment.status}`) })
      );
      expect(
        requests.filter((r) => r.operationName === "ApproveDeployment").map((r) => r.variables)
      ).toEqual([{ input: { id: deployment.id } }, { input: { id: deployment.id } }]);
      expect(requests.find((r) => r.operationName === "GetDeploymentMetrics")?.variables).toEqual({
        windowDays: 30,
      });
      expect(requests.find((r) => r.operationName === "ListDeployments")?.variables).toEqual({
        limit: 100,
      });
      expect(
        requests.some((r) =>
          ["ListAgentTasksPage", "ListPendingHumanGates", "ListSecretChangeProposals"].includes(
            r.operationName ?? ""
          )
        )
      ).toBe(false);
      expect(w.onError).not.toHaveBeenCalled();
      hook.unmount();
      client.stop();
    }
  );
});
