import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { parse as parseIcu } from "@formatjs/icu-messageformat-parser";
import { render, screen } from "@testing-library/react";
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
import { ActiveOrgProvider } from "@/graphql/identity/identity.hooks";
import { AgentInteractionMapView, buildInteractionGraph } from "./AgentInteractionMap";
import { agentRunSteps, MAX_INTERACTION_STEPS } from "./agent-run-steps";
import { AgentRunDetail } from "./AgentRunDetail";
import {
  INTERACTIONS,
  INTERACTION_MAP,
  MANY_INTERACTIONS,
  RUN_DETAIL,
  TASK_ID,
} from "./agent-runs.fixtures";
import { useAgentInteractionMap } from "./use-agent-interaction-map";
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
const orgId = "71366f2c-4458-4c97-bf4a-476f21a25a82";
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
function harness(locale: string, refused = false, items = INTERACTIONS) {
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
        if (refused && request.operationName === "AgentTaskInteractions")
          throw new Error("RAW_INTERACTION_DIAGNOSTIC");
        const result = await execute({
          schema,
          document,
          variableValues: request.variables,
          fieldResolver,
          rootValue: {
            astroliftOrganizations: [{ id: orgId, slug: "literal-org", name: "LITERAL_ORG" }],
            agentTaskInteractions: items,
          },
        });
        expect("errors" in result ? result.errors : undefined).toBeUndefined();
        return new Response(JSON.stringify(result), {
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
          <ActiveOrgProvider>
            <TooltipProvider>{children}</TooltipProvider>
          </ActiveOrgProvider>
        </ApolloProvider>
      </NextIntlClientProvider>
    );
  }
  return { Wrapper, requests, errors };
}
const translator = (locale: string) =>
  createTranslator({ locale, messages: catalogs[locale], namespace: "agentActivity" });
function Connected() {
  return <AgentInteractionMapView {...useAgentInteractionMap(TASK_ID, "completed")} />;
}
function leaves(value: Record<string, unknown>, prefix = ""): Record<string, string> {
  return Object.fromEntries(
    Object.entries(value).flatMap(([key, v]) =>
      typeof v === "string"
        ? [[prefix + key, v]]
        : Object.entries(leaves(v as Record<string, unknown>, prefix + key + "."))
    )
  );
}
describe.each(locales)("agent timeline and map in %s", (locale) => {
  it("localizes timeline labels and folded counts without changing IDs, ordering, duration or call names", () => {
    const t = translator(locale);
    const steps = agentRunSteps(
      RUN_DETAIL.task!,
      MANY_INTERACTIONS,
      RUN_DETAIL.now,
      (key, values) => t(key, values)
    );
    expect(steps[0].name).toBe(t("statuses.queued"));
    expect(steps[1].name).toBe(t("statuses.running"));
    expect(steps[2].name).toBe(t("earlierCalls", { count: 20 }));
    expect(steps[2].detail).toBe(t("earlierDetail"));
    expect(steps).toHaveLength(3 + MAX_INTERACTION_STEPS);
    expect(steps.at(-1)?.id).toBe(MANY_INTERACTIONS.at(-1)?.id);
    expect(steps.at(-1)?.name).toBe(MANY_INTERACTIONS.at(-1)?.name);
    expect(steps.map(({ id, state, durationMs }) => ({ id, state, durationMs }))).toEqual(
      agentRunSteps(RUN_DETAIL.task!, MANY_INTERACTIONS, RUN_DETAIL.now).map(
        ({ id, state, durationMs }) => ({ id, state, durationMs })
      )
    );
  });
  it("renders the localized folded timeline through the actual run view", () => {
    const h = harness(locale);
    const t = translator(locale);
    render(
      <AgentRunDetail
        {...RUN_DETAIL}
        interactions={{ ...INTERACTION_MAP, interactions: MANY_INTERACTIONS }}
      />,
      { wrapper: h.Wrapper }
    );
    expect(screen.getByText(t("earlierCalls", { count: 20 }))).toBeInTheDocument();
    expect(screen.getByText(t("earlierDetail"))).toBeInTheDocument();
    expect(screen.getByText(MANY_INTERACTIONS.at(-1)!.name)).toBeInTheDocument();
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("renders current-SDL interaction reads under the current organization and exact task", async () => {
    const h = harness(locale);
    const t = translator(locale);
    render(<Connected />, { wrapper: h.Wrapper });
    expect(await screen.findByText("crm.search_accounts")).toBeInTheDocument();
    for (const kind of ["control_api", "tool_call", "gate", "signal"])
      expect(screen.getByText(t(`hubs.${kind}`))).toBeInTheDocument();
    expect(screen.getByText(t("statuses.completed"))).toBeInTheDocument();
    expect(h.requests.find((r) => r.operationName === "AgentTaskInteractions")?.variables).toEqual({
      orgId,
      taskId: TASK_ID,
      since: null,
      limit: 200,
    });
    expect(h.requests.every((r) => r.operationName !== "CancelTask")).toBe(true);
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("preserves refused server diagnostics and does not report empty success", async () => {
    const h = harness(locale, true);
    const t = translator(locale);
    render(<Connected />, { wrapper: h.Wrapper });
    expect(await screen.findByText("RAW_INTERACTION_DIAGNOSTIC")).toBeInTheDocument();
    expect(screen.queryByText(t("emptyTitle"))).not.toBeInTheDocument();
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("renders localized empty/loading states only when metadata is absent", () => {
    const h = harness(locale);
    const t = translator(locale);
    const { rerender } = render(
      <AgentInteractionMapView {...INTERACTION_MAP} interactions={[]} loading />,
      { wrapper: h.Wrapper }
    );
    expect(screen.getByRole("status", { name: t("loading") })).toBeInTheDocument();
    expect(screen.queryByText(t("emptyTitle"))).not.toBeInTheDocument();
    rerender(<AgentInteractionMapView {...INTERACTION_MAP} interactions={[]} />);
    expect(screen.getByText(t("emptyTitle"))).toBeInTheDocument();
    expect(screen.getByText(t("emptyDescription"))).toBeInTheDocument();
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("keeps literal unknown status/kind/name values, graph IDs and malformed date neutrality", () => {
    const t = translator(locale);
    const items = [
      {
        ...INTERACTIONS[2],
        id: "literal-id",
        name: "literal_endpoint /v1.x",
        occurredAt: "malformed",
      },
      { ...INTERACTIONS[2], id: "unnamed-id", name: "", occurredAt: "malformed" },
    ];
    const relative = vi.fn();
    const graph = buildInteractionGraph(items, "__proto__", true, (k, v) => t(k, v), relative);
    expect(graph.nodes.find((n) => n.id === "task")?.data.sublabel).toBe("__proto__");
    expect(graph.nodes.find((n) => n.id === "task")?.data.tone).toBe("muted");
    expect(
      graph.nodes.find((n) => n.id === "leaf:tool_call:literal_endpoint /v1.x")?.data.label
    ).toBe(items[0].name);
    expect(graph.nodes.find((n) => n.id === "leaf:tool_call:(unnamed)")?.data.label).toBe(
      t("unnamed")
    );
    expect(relative).not.toHaveBeenCalled();
    expect(graph.nodes.find((n) => n.id === "kind:tool_call")?.data.sublabel).toBe(
      t("calls", { count: 2 })
    );
    const steps = agentRunSteps(
      RUN_DETAIL.task!,
      [
        { ...items[0], kind: "future_kind_v2" },
        { ...items[1], kind: "__proto__", name: "RAW_NAME" },
      ],
      RUN_DETAIL.now,
      (k, v) => t(k, v)
    );
    expect(steps[2].detail).toBe("future_kind_v2");
    expect(steps[3].detail).toBe("__proto__");
    expect(steps[3].name).toBe("RAW_NAME");
  });
  it("changes graph labels on locale switch while preserving exact node and edge identity", () => {
    const h = harness(locale);
    const { rerender } = render(
      <AgentInteractionMapView {...INTERACTION_MAP} taskStatus="future_status_v2" />,
      { wrapper: h.Wrapper }
    );
    expect(screen.getByText(translator(locale)("hubs.control_api"))).toBeInTheDocument();
    const next = locale === "ja" ? "fr" : "ja";
    rerender(
      <NextIntlClientProvider locale={next} messages={catalogs[next]} timeZone="UTC">
        <AgentInteractionMapView {...INTERACTION_MAP} taskStatus="future_status_v2" />
      </NextIntlClientProvider>
    );
    expect(screen.getByText(translator(next)("hubs.control_api"))).toBeInTheDocument();
    expect(screen.queryByText(translator(locale)("hubs.control_api"))).not.toBeInTheDocument();
    expect(screen.getByText("future_status_v2")).toBeInTheDocument();
    expect(screen.getByText("crm.search_accounts")).toBeInTheDocument();
    const before = buildInteractionGraph(
      INTERACTIONS,
      "running",
      false,
      (k, v) => translator(locale)(k, v),
      () => "AGE"
    );
    const after = buildInteractionGraph(
      INTERACTIONS,
      "running",
      false,
      (k, v) => translator(next)(k, v),
      () => "AGE"
    );
    expect(after.nodes.map((n) => n.id)).toEqual(before.nodes.map((n) => n.id));
    expect(after.edges).toEqual(before.edges);
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("parses every message with matching ICU arguments and renders plural counts", () => {
    const t = translator(locale);
    const en = leaves(catalogs.en.agentActivity);
    const actual = leaves(catalogs[locale].agentActivity);
    expect(Object.keys(actual).sort()).toEqual(Object.keys(en).sort());
    for (const [key, value] of Object.entries(actual)) {
      expect(() => parseIcu(value)).not.toThrow();
      expect(
        (value.match(/\{(\w+)[,}]/g) ?? []).map((s) => s.slice(1, -1)).filter((s) => s === "count")
      ).toEqual(
        (en[key].match(/\{(\w+)[,}]/g) ?? [])
          .map((s) => s.slice(1, -1))
          .filter((s) => s === "count")
      );
    }
    for (const count of [0, 1, 2, 5])
      for (const key of ["calls", "earlierCalls", "repetitions"])
        expect(t(key, { count })).not.toContain("{count");
  });
});
