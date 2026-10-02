import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { parse as parseIcu } from "@formatjs/icu-messageformat-parser";
import { render, screen, waitFor, within } from "@testing-library/react";
import {
  buildSchema,
  execute,
  getNamedType,
  isEnumType,
  isListType,
  isNonNullType,
  isObjectType,
  parse,
  validate,
  type GraphQLFieldResolver,
} from "graphql";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useLocalSettingsSection } from "@/components/settings/use-settings-section";
import { AgentOverviewView } from "./AgentOverview";
import { AgentTabSections } from "./AgentTabSections";
import { AgentConfigurationTab } from "./AgentConfigurationTab";
import { useAgentOverview } from "./use-agent-overview";
import { AGENT, DETAIL, OVERVIEW, TASKS } from "./agent-detail-shell.fixtures";
import { AGENT_TAB_SECTIONS, agentSectionHref } from "./agent-tabs-model";
import { runDot } from "./agent-runs-list";
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
const fieldResolver: GraphQLFieldResolver<Record<string, unknown>, unknown> = (
  object,
  _args,
  _context,
  info
) => {
  if (object && info.fieldName in object) return object[info.fieldName];
  const type = getNamedType(info.returnType);
  const wrapped = isNonNullType(info.returnType) ? info.returnType.ofType : info.returnType;
  if (isListType(wrapped)) return [];
  if (isObjectType(type)) return {};
  if (isEnumType(type)) return type.getValues()[0].name;
  return type.name === "Boolean" ? false : ["Int", "Float"].includes(type.name) ? 0 : "";
};
const orgId = "71366f2c-4458-4c97-bf4a-476f21a25a82";
function harness(locale: string, refused = false) {
  const errors = vi.fn();
  const requests: { operationName: string; variables: Record<string, unknown> }[] = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://test.invalid/graphql",
      fetch: async (_url, init) => {
        const request = JSON.parse(String(init?.body));
        requests.push(request);
        const document = parse(request.query);
        expect(validate(schema, document)).toEqual([]);
        if (refused && request.operationName === "GetAgentDetail")
          throw new Error("RAW_DETAIL_DIAGNOSTIC");
        const response = await execute({
          schema,
          document,
          variableValues: request.variables,
          fieldResolver,
          rootValue: {
            agent: DETAIL,
            agentFleet: [AGENT],
            agentTasksPage: {
              items: TASKS.slice(0, 2),
              totalCount: 2,
              nextCursor: null,
              hasMore: false,
            },
          },
        });
        expect("errors" in response ? response.errors : undefined).toBeUndefined();
        return new Response(JSON.stringify(response), {
          headers: { "Content-Type": "application/json" },
        });
      },
    }),
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        timeZone="UTC"
        onError={errors}
      >
        <ApolloProvider client={client}>
          <TooltipProvider>{children}</TooltipProvider>
        </ApolloProvider>
      </NextIntlClientProvider>
    );
  }
  return { Wrapper, requests, errors };
}
const translator = (locale: string, namespace: string) =>
  createTranslator({ locale, messages: catalogs[locale], namespace });
describe.each(locales)("agent overview and sections in %s", (locale) => {
  it("renders current-SDL network metadata with localized runtime/recent labels and exact task/configuration targets", async () => {
    const h = harness(locale);
    const t = translator(locale, "agentOverview");
    function Connected() {
      const data = useAgentOverview({ agent: AGENT, orgId });
      return <AgentOverviewView {...data} fleet={null} />;
    }
    render(<Connected />, { wrapper: h.Wrapper });
    expect(await screen.findAllByText(TASKS[0].id)).toHaveLength(2);
    for (const key of ["latestRun", "runtime", "recentRuns", "image", "brief", "skills", "tools"])
      expect(screen.getByText(t(key))).toBeInTheDocument();
    expect(screen.getByRole("link", { name: t("openRun") })).toHaveAttribute(
      "href",
      `/agents/runs/${TASKS[0].id}`
    );
    expect(screen.getByRole("link", { name: t("configuration") })).toHaveAttribute(
      "href",
      `/agents/${AGENT.slug}/configuration`
    );
    await waitFor(() => expect(screen.getByText(DETAIL.imageRef)).toBeInTheDocument());
    expect(
      h.requests.some(
        (r) =>
          r.operationName === "GetAgentDetail" &&
          r.variables.orgId === orgId &&
          r.variables.slug === AGENT.slug
      )
    ).toBe(true);
    expect(
      h.requests.some(
        (r) => r.operationName === "ListAgentTasksPage" && r.variables.workloadId === AGENT.id
      )
    ).toBe(true);
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("keeps server read diagnostics literal and does not invent runtime metadata", async () => {
    const h = harness(locale, true);
    const t = translator(locale, "agentOverview");
    function Connected() {
      const data = useAgentOverview({ agent: AGENT, orgId });
      return <AgentOverviewView {...data} fleet={null} />;
    }
    render(<Connected />, { wrapper: h.Wrapper });
    expect(await screen.findByText("RAW_DETAIL_DIAGNOSTIC")).toBeInTheDocument();
    expect(screen.getByText(t("runtime"))).toBeInTheDocument();
    expect(screen.queryByText(DETAIL.imageRef)).not.toBeInTheDocument();
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("localizes known statuses and neutral malformed dates, preserving unknown/prototype tokens and result identities", () => {
    const h = harness(locale);
    const t = translator(locale, "agentOverview");
    const { rerender } = render(
      <AgentOverviewView
        {...OVERVIEW}
        fleet={null}
        runs={{ ...OVERVIEW.runs, rows: [{ ...TASKS[0], status: "RUNNING", startedAt: null }] }}
      />,
      { wrapper: h.Wrapper }
    );
    expect(screen.getAllByText(t("statuses.running")).length).toBeGreaterThan(0);
    expect(screen.getByText(t("notYet"))).toBeInTheDocument();
    rerender(
      <AgentOverviewView
        {...OVERVIEW}
        fleet={null}
        agent={{ ...AGENT, runFamily: "FUTURE_FAMILY", runMode: "FUTURE_MODE" }}
        runs={{
          ...OVERVIEW.runs,
          rows: [
            { ...TASKS[0], status: "__proto__", startedAt: "malformed", createdAt: "malformed" },
          ],
        }}
      />
    );
    expect(screen.getAllByText("__proto__")).toHaveLength(2);
    expect(runDot("__proto__")).toBe("muted");
    expect(runDot("constructor")).toBe("muted");
    expect(screen.getAllByText(t("unknownDate"))).toHaveLength(2);
    expect(screen.getByText("FUTURE_FAMILY · FUTURE_MODE")).toBeInTheDocument();
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("translates fixed section labels while preserving every scoped encoded section destination", () => {
    const h = harness(locale);
    const t = translator(locale, "agentNavigation");
    const slug = "literal/agent 空";
    const { rerender } = render(
      <AgentTabSections slug={slug} tab="configuration" active="model-access">
        <div>LITERAL_BODY</div>
      </AgentTabSections>,
      { wrapper: h.Wrapper }
    );
    for (const tab of ["configuration", "skills", "logs", "access", "settings"] as const) {
      rerender(
        <AgentTabSections slug={slug} tab={tab} active={AGENT_TAB_SECTIONS[tab]![0].id}>
          <div>LITERAL_BODY</div>
        </AgentTabSections>
      );
      const nav = within(screen.getByRole("navigation", { name: t("sectionsLabel") }));
      for (const section of AGENT_TAB_SECTIONS[tab]!) {
        expect(nav.getByRole("link", { name: t(`sections.${section.id}`) })).toHaveAttribute(
          "href",
          agentSectionHref(slug, tab, section.id)
        );
      }
    }
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("configuration mounts only the selected slot with localized heading and description", () => {
    const h = harness(locale);
    const t = translator(locale, "agentNavigation");
    function Configuration() {
      return (
        <AgentConfigurationTab
          section={useLocalSettingsSection("model-access")}
          slots={{ build: <div>RAW_BUILD_SLOT</div>, "model-access": <div>RAW_MODEL_SLOT</div> }}
        />
      );
    }
    render(<Configuration />, { wrapper: h.Wrapper });
    expect(screen.getByText("RAW_MODEL_SLOT")).toBeInTheDocument();
    expect(screen.queryByText("RAW_BUILD_SLOT")).not.toBeInTheDocument();
    expect(screen.getByText(t("descriptions.model-access"))).toBeInTheDocument();
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("renders localized empty/runtime/fleet copy and complete ICU counts without transforming the agent name", () => {
    const h = harness(locale);
    const t = translator(locale, "agentOverview");
    const { rerender } = render(
      <AgentOverviewView
        {...OVERVIEW}
        fleet={null}
        detail={{ ...DETAIL, imageRef: "", brief: null, skills: [] }}
        runs={{ ...OVERVIEW.runs, rows: [], count: 0 }}
      />,
      { wrapper: h.Wrapper }
    );
    expect(screen.getByText(t("notRun"))).toBeInTheDocument();
    expect(screen.getByText(t("notSet"))).toBeInTheDocument();
    expect(screen.getByText(t("noneYet"))).toBeInTheDocument();
    rerender(<AgentOverviewView {...OVERVIEW} />);
    expect(
      screen.getByText(
        t("fleetDescription", { name: AGENT.name, count: OVERVIEW.fleet!.agents.length })
      )
    ).toBeInTheDocument();
    for (const n of [0, 1, 2, 5])
      expect(t("fleetDescription", { name: "LITERAL_NAME", count: n })).toContain("LITERAL_NAME");
    for (const namespace of ["agentOverview", "agentNavigation"]) {
      const flatten = (v: Record<string, unknown>, prefix = ""): Record<string, string> =>
        Object.fromEntries(
          Object.entries(v).flatMap(([key, value]) =>
            typeof value === "string"
              ? [[prefix + key, value]]
              : Object.entries(flatten(value as Record<string, unknown>, prefix + key + "."))
          )
        );
      const source = flatten(catalogs.en[namespace]);
      const actual = flatten(catalogs[locale][namespace]);
      expect(Object.keys(actual)).toEqual(Object.keys(source));
      for (const [key, message] of Object.entries(actual)) {
        expect(parseIcu(message)).toBeDefined();
        const args = (s: string) => [...s.matchAll(/\{(\w+)[,}]/g)].map((m) => m[1]).sort();
        expect(args(message)).toEqual(args(source[key]));
      }
    }
    expect(h.errors).not.toHaveBeenCalled();
  });
});
