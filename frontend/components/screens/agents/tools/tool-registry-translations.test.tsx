import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { parse as parseIcu } from "@formatjs/icu-messageformat-parser";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  buildSchema,
  execute,
  parse,
  validate,
  getNamedType,
  isNonNullType,
  isListType,
  isObjectType,
  isEnumType,
  type GraphQLFieldResolver,
} from "graphql";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ToolRegistryPage from "@/app/(app)/agents/tools/page";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ActiveOrgProvider } from "@/graphql/identity/identity.hooks";
import { locales } from "@/i18n/config";
import { agentsCrumbs } from "@/components/screens/agents/skills/catalog";
import { parseSort } from "@/components/list/list-state";
import {
  TOOLS_LIST,
  localizedToolsList,
  localizedToolsCrumbs,
  localizedToolAdapter,
  toolDefsPageVariables,
} from "./tools-list";
import { TOOLS, serveTools } from "./agent-tools.fixtures";
import type { ToolRegistryTool } from "./use-tool-registry";

const route = vi.hoisted(() => ({ query: "", replace: vi.fn(), listeners: new Set<() => void>() }));
vi.mock("next/navigation", async () => {
  const { useSyncExternalStore } = await import("react");
  return {
    useSearchParams: () =>
      new URLSearchParams(
        useSyncExternalStore(
          (listener) => {
            route.listeners.add(listener);
            return () => {
              route.listeners.delete(listener);
            };
          },
          () => route.query,
          () => route.query
        )
      ),
    usePathname: () => "/agents/tools",
    useRouter: () => ({
      replace: (href: string) => {
        route.replace(href);
        route.query = new URL(href, "https://test.invalid").search.slice(1);
        route.listeners.forEach((listener) => listener());
      },
    }),
  };
});
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const tFor = (locale: string, namespace = "agentToolRegistry") =>
  createTranslator({ locale, messages: catalogs[locale], namespace });
const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
const orgId = "71366f2c-4458-4c97-bf4a-476f21a25a82";
const rows: ToolRegistryTool[] = Array.from({ length: 60 }, (_, i) => ({
  ...TOOLS[i % TOOLS.length],
  id: `71366f2c-4458-4c97-bf4a-${String(i + 1).padStart(12, "0")}`,
  name: `RAW_TOOL_${String(i + 1).padStart(2, "0")}`,
  slug: `literal-tool-${i + 1}`,
  description: `RAW_DESCRIPTION_${i + 1}`,
  handlerRef: `literal.handlers.tool_${i + 1}`,
  skillSlug: "literal-skill",
  skillName: "RAW_SKILL_NAME",
  createdAt: "2026-09-01T12:00:00Z",
}));
const fieldResolver: GraphQLFieldResolver<Record<string, unknown>, unknown> = (
  object,
  args,
  _context,
  info
) => {
  if (object && info.fieldName in object) {
    const value = object[info.fieldName];
    return typeof value === "function" ? value(args) : value;
  }
  const type = getNamedType(info.returnType),
    wrapped = isNonNullType(info.returnType) ? info.returnType.ofType : info.returnType;
  if (isListType(wrapped)) return [];
  if (isObjectType(type)) return {};
  if (isEnumType(type)) return type.getValues()[0].name;
  return type.name === "Boolean" ? false : ["Int", "Float"].includes(type.name) ? 0 : "";
};
function harness(locale: string, items = rows, refused = false) {
  let fail = refused;
  const requests: Array<{ operationName: string; variables: Record<string, unknown> }> = [],
    errors = vi.fn();
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://test.invalid/graphql",
      fetch: async (_url, init) => {
        const request = JSON.parse(String(init?.body));
        requests.push(request);
        const document = parse(request.query);
        expect(validate(schema, document)).toEqual([]);
        if (fail && request.operationName === "ToolDefsListPage")
          throw new Error("RAW_TOOL_READ_DIAGNOSTIC");
        const result = await execute({
          schema,
          document,
          variableValues: request.variables,
          fieldResolver,
          rootValue: {
            astroliftOrganizations: [{ id: orgId, name: "RAW_ORG_NAME", slug: "literal-org" }],
            orgToolDefsPage: (args: {
              search: string | null;
              sort: string;
              page: number;
              pageSize: number;
              filter: {
                adapter?: string[] | null;
                skill?: string[] | null;
                builtin?: boolean | null;
                createdBy?: string[] | null;
              } | null;
            }) => {
              const filter = args.filter;
              const filters: Record<string, string> = {};
              if (filter?.skill?.length) filters.skill = filter.skill[0];
              if (filter?.adapter?.length) filters.adapter = filter.adapter[0];
              if (typeof filter?.builtin === "boolean")
                filters.builtin = filter.builtin ? "1" : "0";
              if (filter?.createdBy?.length) filters.createdBy = filter.createdBy[0];
              const page = serveTools(items, {
                filters,
                q: args.search ?? "",
                sort: parseSort(args.sort),
                page: args.page,
                pageSize: args.pageSize,
              });
              return {
                items: page.rows,
                totalCount: page.totalCount,
                page: args.page,
                pageSize: args.pageSize,
              };
            },
          },
        });
        expect(
          "errors" in result ? result.errors?.map((error) => error.message) : undefined
        ).toBeUndefined();
        return new Response(JSON.stringify(result), {
          headers: { "Content-Type": "application/json" },
        });
      },
    }),
  });
  const element = (nextLocale = locale) => (
    <NextIntlClientProvider
      locale={nextLocale}
      messages={catalogs[nextLocale]}
      timeZone="UTC"
      onError={errors}
    >
      <ApolloProvider client={client}>
        <ActiveOrgProvider>
          <TooltipProvider>
            <ToolRegistryPage />
          </TooltipProvider>
        </ActiveOrgProvider>
      </ApolloProvider>
    </NextIntlClientProvider>
  );
  return {
    client,
    requests,
    errors,
    element,
    fail: () => {
      fail = true;
    },
    recover: () => {
      fail = false;
    },
  };
}
const pageRequests = (h: ReturnType<typeof harness>) =>
  h.requests.filter((request) => request.operationName === "ToolDefsListPage");
const expected = { orgId, search: null, filter: null, sort: "-created", page: 1, pageSize: 25 };
beforeEach(() => {
  route.query = "";
  route.replace.mockClear();
  window.localStorage.clear();
});

describe.each(locales)("connected tool registry in %s", (locale) => {
  it("localizes metadata, views and server filter labels without changing identity, tokens or paging", () => {
    const t = tFor(locale),
      def = localizedToolsList(t);
    expect(def.id).toBe(TOOLS_LIST.id);
    expect(def.defaultSort).toEqual(TOOLS_LIST.defaultSort);
    expect(def.pageSizes).toEqual([25, 50, 100]);
    expect(def.paging).toBe("numbered");
    expect(def.fields.map((field) => field.key)).toEqual(["skill", "adapter"]);
    expect(def.fields[1].options?.map((option) => option.value)).toEqual([
      "python_fn",
      "http_endpoint",
      "mcp_server",
    ]);
    expect(def.fields.map((field) => field.label)).toEqual([t("skill"), t("adapter")]);
    expect(def.fields[1].options?.map((option) => option.label)).toEqual([
      t("python_fn"),
      t("http_endpoint"),
      t("mcp_server"),
    ]);
    expect(def.views.map((view) => ({ key: view.key, filters: view.filters }))).toEqual(
      TOOLS_LIST.views.map((view) => ({ key: view.key, filters: view.filters }))
    );
    expect(def.views.map((view) => view.label)).toEqual([
      t("all"),
      t("mine"),
      t("builtin"),
      t("custom"),
    ]);
    expect(def.views[1].note).toBe(t("mineNote"));
    expect(def.searchPlaceholder).toBe(t("search"));
    expect(
      toolDefsPageVariables({
        q: " literal-query ",
        filters: { adapter: "python_fn", skill: "literal-skill", builtin: "0", createdBy: "me" },
        sort: [],
        page: 2,
        pageSize: 50,
      })
    ).toEqual({
      search: "literal-query",
      filter: {
        adapter: ["python_fn"],
        skill: ["literal-skill"],
        builtin: false,
        createdBy: ["me"],
      },
      sort: "-created",
      page: 2,
      pageSize: 50,
    });
  });
  it("translates only known breadcrumb labels and retains all original navigation targets", () => {
    const crumbs = localizedToolsCrumbs(tFor(locale)),
      original = agentsCrumbs("tools");
    expect(crumbs[0].label).toBe(tFor(locale)("agents"));
    expect(crumbs[1].label).toBe(tFor(locale)("title"));
    expect(crumbs[0].switcher?.map(({ href, active }) => ({ href, active }))).toEqual(
      original[0].switcher?.map(({ href, active }) => ({ href, active }))
    );
    expect(crumbs[0].switcher?.map((item) => item.label)).not.toContain(undefined);
    for (const token of ["__proto__", "constructor", "toString", "RAW_FUTURE_ADAPTER"]) {
      expect(localizedToolAdapter(tFor(locale), token)).toBe(token);
    }
  });
  it("renders the real page adapter from HTTP/current SDL, with localized columns and literal row data", async () => {
    const h = harness(locale),
      t = tFor(locale);
    render(h.element());
    expect(await screen.findByRole("link", { name: /RAW_TOOL_01/ })).toHaveAttribute(
      "href",
      `/agents/tools/${rows[0].id}`
    );
    for (const key of ["tool", "skill", "adapter", "description", "handler", "registered"]) {
      expect(screen.getByRole("columnheader", { name: new RegExp(t(key)) })).toBeInTheDocument();
    }
    expect(screen.getByText(t("context"))).toBeInTheDocument();
    expect(screen.getByPlaceholderText(t("search"))).toBeInTheDocument();
    for (const key of ["all", "mine", "builtin", "custom"]) {
      expect(screen.getByRole("link", { name: t(key) })).toBeInTheDocument();
    }
    expect(screen.getAllByText("RAW_SKILL_NAME").length).toBeGreaterThan(0);
    expect(screen.getByText("literal.handlers.tool_1")).toBeInTheDocument();
    for (const key of [
      "python_fn",
      "http_endpoint",
      "mcp_server",
      "unknown",
      "builtin",
      "organization",
    ]) {
      expect(screen.getAllByText(t(key)).length).toBeGreaterThan(0);
    }
    expect(screen.queryByRole("link", { name: /RAW_TOOL_26/ })).not.toBeInTheDocument();
    expect(pageRequests(h).map((r) => r.variables)).toEqual([expected]);
    expect(h.errors).not.toHaveBeenCalled();
    h.client.stop();
  });
  it("shows the successful empty catalog with a real skills link", async () => {
    const h = harness(locale, []),
      t = tFor(locale);
    render(h.element());
    expect(await screen.findByText(t("emptyTitle"))).toBeInTheDocument();
    expect(screen.getByText(t("emptyDescription"))).toBeInTheDocument();
    expect(screen.getByRole("link", { name: t("goToSkills") })).toHaveAttribute(
      "href",
      "/agents/skills"
    );
    expect(h.errors).not.toHaveBeenCalled();
    h.client.stop();
  });
  it("preserves failed-read diagnostics and retries in the same org instead of showing an empty catalog", async () => {
    const h = harness(locale, rows, true),
      t = tFor(locale);
    render(h.element());
    expect(await screen.findByText("RAW_TOOL_READ_DIAGNOSTIC")).toBeInTheDocument();
    expect(screen.queryByText(t("emptyTitle"))).not.toBeInTheDocument();
    h.recover();
    await userEvent.click(
      screen.getByRole("button", { name: tFor(locale, "shared.table")("retry") })
    );
    expect(await screen.findByRole("link", { name: /RAW_TOOL_01/ })).toBeInTheDocument();
    expect(pageRequests(h).map((r) => r.variables)).toEqual([expected, expected]);
    expect(h.errors).not.toHaveBeenCalled();
    h.client.stop();
  });
  it("shows a failed refresh diagnostic even with cached rows and recovers without false empty guidance", async () => {
    const h = harness(locale),
      t = tFor(locale);
    render(h.element());
    await screen.findByRole("link", { name: /RAW_TOOL_01/ });
    h.fail();
    await act(async () => {
      await h.client.refetchQueries({ include: ["ToolDefsListPage"] }).catch(() => undefined);
    });
    expect(await screen.findByText("RAW_TOOL_READ_DIAGNOSTIC")).toBeInTheDocument();
    expect(screen.queryByText(t("emptyTitle"))).not.toBeInTheDocument();
    h.recover();
    await userEvent.click(
      screen.getByRole("button", { name: tFor(locale, "shared.table")("retry") })
    );
    expect(await screen.findByRole("link", { name: /RAW_TOOL_01/ })).toBeInTheDocument();
    expect(pageRequests(h).map((r) => r.variables)).toEqual([expected, expected, expected]);
    expect(h.errors).not.toHaveBeenCalled();
    h.client.stop();
  });
  it("sends literal adapter, skill and search values selected through the localized controls", async () => {
    const h = harness(locale),
      t = tFor(locale),
      shared = tFor(locale, "shared.list");
    render(h.element());
    await screen.findByRole("link", { name: /RAW_TOOL_01/ });
    await userEvent.click(screen.getByRole("button", { name: shared("filter") }));
    await userEvent.click(screen.getByRole("option", { name: new RegExp(t("adapter")) }));
    await userEvent.click(screen.getByRole("option", { name: t("python_fn") }));
    await waitFor(() =>
      expect(pageRequests(h).at(-1)?.variables).toEqual({
        ...expected,
        filter: { adapter: ["python_fn"] },
      })
    );
    await userEvent.click(screen.getByRole("button", { name: shared("filter") }));
    await userEvent.click(screen.getByRole("option", { name: new RegExp(t("skill")) }));
    await userEvent.type(
      screen.getByRole("textbox", { name: shared("fieldValue", { field: t("skill") }) }),
      "literal-skill{enter}"
    );
    await waitFor(() =>
      expect(pageRequests(h).at(-1)?.variables).toEqual({
        ...expected,
        filter: { skill: ["literal-skill"], adapter: ["python_fn"] },
      })
    );
    await userEvent.type(screen.getByPlaceholderText(t("search")), "RAW_TOOL_06");
    await waitFor(() =>
      expect(pageRequests(h).at(-1)?.variables).toEqual({
        ...expected,
        search: "RAW_TOOL_06",
        filter: { skill: ["literal-skill"], adapter: ["python_fn"] },
      })
    );
    expect(await screen.findByRole("link", { name: /RAW_TOOL_06/ })).toHaveAttribute(
      "href",
      `/agents/tools/${rows[5].id}`
    );
    expect(screen.queryByRole("link", { name: /RAW_TOOL_01/ })).not.toBeInTheDocument();
    expect(h.errors).not.toHaveBeenCalled();
    h.client.stop();
  });
  it("keeps Mine server-scoped and discloses the registration-history limitation", async () => {
    route.query = "view=mine";
    const h = harness(locale),
      t = tFor(locale);
    render(h.element());
    expect(await screen.findByRole("link", { name: /RAW_TOOL_01/ })).toBeInTheDocument();
    expect(screen.getByText(t("mineNote"))).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /RAW_TOOL_02/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /RAW_TOOL_05/ })).not.toBeInTheDocument();
    expect(pageRequests(h)[0].variables).toEqual({ ...expected, filter: { createdBy: ["me"] } });
    h.client.stop();
  });
  for (const [view, builtin] of [
    ["builtin", true],
    ["custom", false],
  ] as const) {
    it(`answers ${view} through the actual server-filter contract`, async () => {
      route.query = `view=${view}`;
      const h = harness(locale),
        t = tFor(locale);
      render(h.element());
      expect(
        await screen.findByRole("link", { name: builtin ? /RAW_TOOL_04/ : /RAW_TOOL_01/ })
      ).toBeInTheDocument();
      expect(screen.getByRole("link", { name: t(view) })).toHaveAttribute("aria-current", "page");
      if (!builtin) expect(screen.getByRole("link", { name: /RAW_TOOL_05/ })).toBeInTheDocument();
      expect(
        screen.queryByRole("link", { name: builtin ? /RAW_TOOL_01/ : /RAW_TOOL_04/ })
      ).not.toBeInTheDocument();
      expect(pageRequests(h)[0].variables).toEqual({ ...expected, filter: { builtin } });
      h.client.stop();
    });
  }
  it("reads the next page through actual localized paging controls without silently filtering a capped page", async () => {
    const h = harness(locale),
      pagination = tFor(locale, "shared.pagination");
    render(h.element());
    await screen.findByRole("link", { name: /RAW_TOOL_01/ });
    await userEvent.click(screen.getByRole("button", { name: pagination("nextPage") }));
    expect(await screen.findByRole("link", { name: /RAW_TOOL_26/ })).toHaveAttribute(
      "href",
      `/agents/tools/${rows[25].id}`
    );
    expect(route.replace).toHaveBeenCalledWith("/agents/tools?page=2");
    expect(pageRequests(h).map((r) => r.variables)).toEqual([expected, { ...expected, page: 2 }]);
    expect(
      screen.getByText(pagination("range", { start: 26, end: 50, total: 60 }), {
        normalizer: (value) => value,
      })
    ).toBeInTheDocument();
    h.client.stop();
  });
  it("keeps raw skill/adapter/search/sort tokens server-side and clears filters without losing unrelated URL state", async () => {
    route.query =
      "skill=literal-skill&adapter=mcp_server&q=NO_MATCH_LITERAL&sort=name&page=2&section=literal-section";
    const h = harness(locale),
      t = tFor(locale),
      shared = tFor(locale, "shared.list");
    render(h.element());
    const noun = locale.startsWith("en") ? t("title").toLocaleLowerCase(locale) : t("title");
    expect(await screen.findByText(shared("emptyFiltered", { label: noun }))).toBeInTheDocument();
    expect(pageRequests(h)[0].variables).toEqual({
      ...expected,
      search: "NO_MATCH_LITERAL",
      filter: { skill: ["literal-skill"], adapter: ["mcp_server"] },
      sort: "name",
      page: 2,
    });
    await userEvent.click(screen.getAllByRole("button", { name: shared("clear") })[0]);
    expect(await screen.findByRole("link", { name: /RAW_TOOL_01/ })).toBeInTheDocument();
    expect(
      new URL(route.replace.mock.calls.at(-1)![0], "https://test.invalid").searchParams.get(
        "section"
      )
    ).toBe("literal-section");
    expect(pageRequests(h).at(-1)?.variables).toEqual({ ...expected, sort: "name" });
    h.client.stop();
  });
  it("keeps missing data neutral and malformed timestamps and future/prototype adapter tokens literal", async () => {
    const data = [
      {
        ...rows[0],
        adapter: "__proto__",
        createdAt: "RAW_INVALID_DATE",
        description: "",
        handlerRef: "",
        skillSlug: "",
      },
      { ...rows[1], adapter: "RAW_FUTURE_ADAPTER", createdAt: "", skillIsGlobal: true },
    ];
    const h = harness(locale, data),
      t = tFor(locale);
    render(h.element());
    expect(await screen.findByText("__proto__")).toBeInTheDocument();
    expect(screen.getByText("RAW_FUTURE_ADAPTER")).toBeInTheDocument();
    expect(screen.getAllByText(t("unknownDate"))).toHaveLength(2);
    expect(screen.getByTitle("RAW_INVALID_DATE")).toHaveTextContent(t("unknownDate"));
    expect(screen.getByText(t("noDescription"))).toBeInTheDocument();
    expect(screen.getAllByText(t("none"))).toHaveLength(2);
    expect(screen.getByText(t("global"))).toBeInTheDocument();
    expect(h.errors).not.toHaveBeenCalled();
    h.client.stop();
  });
  it("changes locale without resetting URL state or issuing a differently scoped network read", async () => {
    route.query = "view=mine&pageSize=50&section=literal-section";
    const h = harness("en");
    const view = render(h.element());
    await screen.findByRole("link", { name: /RAW_TOOL_01/ });
    view.rerender(h.element(locale));
    expect(screen.getByPlaceholderText(tFor(locale)("search"))).toBeInTheDocument();
    expect(screen.getByText(tFor(locale)("mineNote"))).toBeInTheDocument();
    expect(route.query).toBe("view=mine&pageSize=50&section=literal-section");
    expect(pageRequests(h).map((r) => r.variables)).toEqual([
      { ...expected, pageSize: 50, filter: { createdBy: ["me"] } },
    ]);
    expect(route.replace).not.toHaveBeenCalled();
    h.client.stop();
  });
  it("keeps the complete ICU key contract in every locale", () => {
    const messages = catalogs[locale].agentToolRegistry,
      t = tFor(locale);
    expect(Object.keys(messages).sort()).toEqual(Object.keys(catalogs.en.agentToolRegistry).sort());
    expect(Object.keys(messages)).toHaveLength(36);
    for (const [key, message] of Object.entries(messages)) {
      expect(() => parseIcu(message as string)).not.toThrow();
      expect(t(key)).toBe(message);
    }
  });
});
