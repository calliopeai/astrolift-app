import { cleanup, render } from "@testing-library/react";
import { getOperationAST, print } from "graphql";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { EMAIL_DETAIL } from "@/components/screens/apps/managed-services/app-managed-services.fixtures";
import { LIST_MANAGED_SERVICE_SUMMARIES_PAGE } from "@/graphql/services/services.queries";

import { EmailDetailSheet } from "./email-detail-sheet";

const state = vi.hoisted(() => ({
  queries: {} as Record<string, unknown>,
  variables: {} as Record<string, unknown>,
  views: [] as Array<{ detail: unknown; loading: boolean; serviceConfig: Record<string, unknown> }>,
}));

vi.mock("@apollo/client/react", async (original) => ({
  ...(await original<typeof import("@apollo/client/react")>()),
  useQuery: (doc: Parameters<typeof getOperationAST>[0], options?: { variables?: unknown }) => {
    const name = getOperationAST(doc)?.name?.value ?? "";
    state.variables[name] = options?.variables;
    return { data: state.queries[name], loading: false, refetch: vi.fn() };
  },
  useMutation: () => [vi.fn(), { loading: false }],
}));
vi.mock("@/hooks/use-install-policy", () => ({ useWithheldCapabilities: () => ({ dns: null }) }));
vi.mock("@/components/screens/apps/managed-services/EmailDetailSheet", () => {
  const view = (props: (typeof state.views)[number]) => {
    state.views.push(props);
    return null;
  };
  return {
    EmailDetailSheetView: view,
    AlertRulesPanelView: view,
    EngagementMetricsPanelView: view,
    MessageLogPanelView: view,
    SenderConfigPanelView: view,
    SuppressionPanelView: view,
    TemplatesPanelView: view,
    TemplateStatsView: view,
  };
});
vi.mock("@/components/screens/email-delivery/EmailDeliveryClient", () => ({
  EmailDeliveryClient: () => null,
}));

beforeEach(() => {
  state.queries = { GetEmailServiceDetail: { astroliftEmailServiceDetail: EMAIL_DETAIL } };
  state.variables = {};
  state.views = [];
});
afterEach(cleanup);

function sheet() {
  return (
    <EmailDetailSheet
      managedServiceId="ms-1"
      serviceName="notifications"
      appSlug="storefront"
      open
      onOpenChange={() => {}}
    />
  );
}

it("the services table asks for no config, nested collections or raw provider text", () => {
  const query = print(LIST_MANAGED_SERVICE_SUMMARIES_PAGE);
  expect(query).toMatch(/astroliftManagedServiceSummariesPage/);
  expect(query).not.toMatch(
    /config|statusError|attachments|volumeBindings|workloadIdentityGrants|editableFields/i
  );
});

it("withholds the writing panels until the exact config read lands", () => {
  render(sheet());
  const pending = state.views.at(-1)!;
  expect(state.variables.GetAppManagedServiceConfig).toEqual({ appSlug: "storefront", id: "ms-1" });
  expect(pending.detail).toBeNull();
  expect(pending.loading).toBe(true);
});

it("refuses a config row for another GUID", () => {
  state.queries.GetAppManagedServiceConfig = {
    astroliftAppManagedService: { id: "ms-other", config: { from_name: "Elsewhere" } },
  };
  render(sheet());
  expect(state.views.at(-1)!.detail).toBeNull();
});

it("hands the panels the exact service's config", () => {
  state.queries.GetAppManagedServiceConfig = {
    astroliftAppManagedService: { id: "ms-1", config: { from_name: "Storefront" } },
  };
  render(sheet());
  const ready = state.views.at(-1)!;
  expect(ready.detail).toEqual(EMAIL_DETAIL);
  expect(ready.loading).toBe(false);
  expect(ready.serviceConfig).toEqual({ from_name: "Storefront" });
});
