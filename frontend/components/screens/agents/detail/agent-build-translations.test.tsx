import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { parse as parseIcu } from "@formatjs/icu-messageformat-parser";
import { render, screen } from "@testing-library/react";
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
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AgentBuildScreen } from "./AgentBuildScreen";
import { useAgentBuild } from "./use-agent-build";
import { BUILD, BUILD_EMPTY, BUILD_LONG } from "./agent-build-run.fixtures";
import { AGENT } from "./agent-detail-shell.fixtures";
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const tFor = (locale: string) =>
  createTranslator({ locale, messages: catalogs[locale], namespace: "agentBuild" });
const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
const orgId = "71366f2c-4458-4c97-bf4a-476f21a25a82";
const agent = { ...AGENT, ...BUILD.agent };
const fieldResolver: GraphQLFieldResolver<Record<string, unknown>, unknown> = (
  object,
  _args,
  _context,
  info
) => {
  if (object && info.fieldName in object) return object[info.fieldName];
  const type = getNamedType(info.returnType),
    wrapped = isNonNullType(info.returnType) ? info.returnType.ofType : info.returnType;
  if (isListType(wrapped)) return [];
  if (isObjectType(type)) return {};
  if (isEnumType(type)) return type.getValues()[0].name;
  return type.name === "Boolean" ? false : ["Int", "Float"].includes(type.name) ? 0 : "";
};
function harness(locale: string, mode: "normal" | "empty" | "refused" = "normal") {
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
        if (mode === "refused" && request.operationName === "GetAgentDetail")
          throw new Error("RAW_BUILD_READ_DIAGNOSTIC");
        const result = await execute({
          schema,
          document,
          variableValues: request.variables,
          fieldResolver,
          rootValue: {
            astroliftWorkload: mode === "empty" ? null : { id: agent.id, ...BUILD.workload },
            astroliftContainers:
              mode === "empty" ? [] : [{ ...BUILD.primary, id: "literal-primary" }],
            agent:
              mode === "empty"
                ? null
                : { ...agent, brief: BUILD.brief, skills: [...BUILD.skills].reverse() },
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
          <TooltipProvider>{children}</TooltipProvider>
        </ApolloProvider>
      </NextIntlClientProvider>
    );
  }
  return { Wrapper, client, requests, errors };
}
function ConnectedBuild() {
  return (
    <AgentBuildScreen {...useAgentBuild(agent, orgId)} skillsHref="/agents/bdr-outreach/skills" />
  );
}
describe.each(locales)("connected agent Build in %s", (locale) => {
  it("executes real hook operations against current SDL and preserves exact target variables, refs and summary routes", async () => {
    const h = harness(locale),
      t = tFor(locale);
    render(<ConnectedBuild />, { wrapper: h.Wrapper });
    expect(await screen.findByText(BUILD.primary!.imageRef)).toBeInTheDocument();
    expect(await screen.findByText(BUILD.skills[0].skill.name)).toBeInTheDocument();
    expect(h.requests.find((r) => r.operationName === "GetAgentDetail")?.variables).toEqual({
      orgId,
      slug: agent.slug,
    });
    expect(h.requests.find((r) => r.operationName === "GetWorkload")?.variables).toEqual({
      appSlug: agent.appSlug,
      slug: agent.slug,
    });
    expect(h.requests.find((r) => r.operationName === "ListContainers")?.variables).toEqual({
      workloadSlug: agent.slug,
    });
    expect(screen.getByRole("link", { name: agent.sourceRepo })).toHaveAttribute(
      "href",
      agent.sourceUrl
    );
    expect(screen.getByRole("link", { name: t("openManifest") })).toHaveAttribute(
      "href",
      `/apps/${agent.appSlug}/workloads/${agent.slug}`
    );
    expect(screen.getByText("job")).toBeInTheDocument();
    expect(screen.getByText("astrolift.toml")).toBeInTheDocument();
    expect(screen.getByText(BUILD.primary!.dockerfilePath)).toBeInTheDocument();
    expect(screen.getByText(BUILD.primary!.buildContext)).toBeInTheDocument();
    expect(screen.getByText(BUILD.brief!.contentHash)).toBeInTheDocument();
    expect(
      screen.getByText(JSON.stringify(BUILD.brief!.config, null, 2), {
        exact: true,
        normalizer: (v) => v,
      })
    ).toBeInTheDocument();
    const skillLinks = screen
      .getAllByRole("link")
      .filter((link) => link.getAttribute("href")?.startsWith("/agents/skills/"));
    expect(skillLinks.map((link) => link.getAttribute("href"))).toEqual(
      BUILD.skills.map((binding) => `/agents/skills/${binding.skill.id}`)
    );
    const viewAll = screen
      .getAllByRole("link")
      .find((link) => link.getAttribute("href") === "/agents/bdr-outreach/skills");
    expect(viewAll).toBeInTheDocument();
    expect(h.errors).not.toHaveBeenCalled();
    h.client.stop();
  });
  it("renders actual empty read states without inventing image/brief/skill data", async () => {
    const h = harness(locale, "empty"),
      t = tFor(locale);
    render(<ConnectedBuild />, { wrapper: h.Wrapper });
    for (const key of ["noContainer", "noBrief", "noSkills"] as const)
      expect(await screen.findByText(t(key))).toBeInTheDocument();
    expect(screen.queryByText(BUILD.primary!.imageRef)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: t("skillRegistry") })).toHaveAttribute(
      "href",
      "/agents/skills"
    );
    expect(h.errors).not.toHaveBeenCalled();
    h.client.stop();
  });
  it("shows failed join feedback rather than an empty success and preserves independent source/image metadata", async () => {
    const h = harness(locale, "refused"),
      t = tFor(locale);
    render(<ConnectedBuild />, { wrapper: h.Wrapper });
    expect(await screen.findByText(t("briefFailed"))).toBeInTheDocument();
    expect(await screen.findByText(t("skillsFailed"))).toBeInTheDocument();
    expect(screen.queryByText(t("noBrief"))).not.toBeInTheDocument();
    expect(screen.queryByText(t("noSkills"))).not.toBeInTheDocument();
    expect(await screen.findByText(BUILD.primary!.imageRef)).toBeInTheDocument();
    expect(screen.getByText(agent.sourceRepo)).toBeInTheDocument();
    expect(h.errors).not.toHaveBeenCalled();
    h.client.stop();
  });
  it("localizes known adapter/skill-state labels while leaving authored names, descriptions, handlers and JSON literal", () => {
    const h = harness(locale),
      t = tFor(locale);
    const assembledAt = new Date(Date.now() - 120000).toISOString();
    render(<AgentBuildScreen {...BUILD} brief={{ ...BUILD.brief!, createdAt: assembledAt }} />, {
      wrapper: h.Wrapper,
    });
    expect(
      screen.getByText(
        new Intl.RelativeTimeFormat(locale, { numeric: "auto" }).format(-2, "minute")
      )
    ).toHaveAttribute("title", assembledAt);
    for (const key of [
      "global",
      "inactive",
      "python_fn",
      "http_endpoint",
      "mcp_server",
      "noTools",
    ] as const)
      expect(screen.getAllByText(t(key)).length).toBeGreaterThan(0);
    for (const binding of BUILD.skills)
      for (const tool of binding.toolDefs) {
        expect(screen.getByText(tool.name)).toBeInTheDocument();
        if (tool.handlerRef) expect(screen.getByText(tool.handlerRef)).toBeInTheDocument();
      }
    expect(screen.getByText(BUILD.skills[0].skill.description)).toBeInTheDocument();
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("keeps future and prototype adapters literal and invalid assembled timestamps neutral", () => {
    const h = harness(locale),
      t = tFor(locale);
    const tool = BUILD_LONG.skills[0].toolDefs[0];
    render(
      <AgentBuildScreen
        {...BUILD_LONG}
        brief={{ ...BUILD_LONG.brief!, createdAt: "RAW_INVALID_DATE" }}
        skills={[
          {
            ...BUILD_LONG.skills[0],
            toolDefs: [
              { ...tool, id: "future", adapter: "future_adapter_v2" },
              { ...tool, id: "proto", adapter: "__proto__" },
            ],
          },
        ]}
      />,
      { wrapper: h.Wrapper }
    );
    expect(screen.getByText("future_adapter_v2")).toBeInTheDocument();
    expect(screen.getByText("__proto__")).toBeInTheDocument();
    expect(screen.getByText(t("unknownDate"))).toHaveAttribute("title", "RAW_INVALID_DATE");
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("formats count and zero/one/many tool ICU forms with the current locale", () => {
    const h = harness(locale),
      t = tFor(locale);
    const { rerender } = render(
      <AgentBuildScreen {...BUILD} skillsHref="/agents/literal/skills" />,
      { wrapper: h.Wrapper }
    );
    for (const count of [0, 1, 2, 1200]) {
      const skill = {
        ...BUILD.skills[0],
        toolDefs: Array.from({ length: count }, () => BUILD.skills[0].toolDefs[0]),
      };
      rerender(
        <AgentBuildScreen {...BUILD} skills={[skill]} skillsHref="/agents/literal/skills" />
      );
      expect(
        screen.getByText(t("toolCount", { count }), { normalizer: (v) => v }).textContent
      ).toContain(new Intl.NumberFormat(locale).format(count));
    }
    expect(h.errors).not.toHaveBeenCalled();
  });
  it("has all 36 nonempty ICU messages with rich technical placeholders intact", () => {
    const keys = Object.keys(catalogs.en.agentBuild).sort(),
      localized = catalogs[locale].agentBuild;
    expect(Object.keys(localized).sort()).toEqual(keys);
    expect(keys).toHaveLength(36);
    for (const text of Object.values(localized) as string[]) {
      expect(text.trim()).not.toBe("");
      expect(() => parseIcu(text)).not.toThrow();
    }
    expect(localized.declared).toContain("{workloadKind}");
    expect(localized.declared).toContain("{filename}");
  });
});
it("keeps encoded app/workload paths exact with non-ASCII and slash characters", () => {
  const h = harness("ja");
  render(
    <AgentBuildScreen
      {...BUILD_EMPTY}
      agent={{ ...BUILD_EMPTY.agent, slug: "literal/空", appSlug: "app/空" }}
    />,
    { wrapper: h.Wrapper }
  );
  expect(screen.getByRole("link", { name: tFor("ja")("openManifest") })).toHaveAttribute(
    "href",
    "/apps/app%2F%E7%A9%BA/workloads/literal%2F%E7%A9%BA"
  );
});
