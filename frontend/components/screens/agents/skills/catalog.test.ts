import { describe, expect, it } from "vitest";

import {
  effectiveFilters,
  parseListState,
  type ListDefinition,
} from "@/components/list/list-state";
import {
  functionsPageVariables,
  FUNCTIONS_LIST,
} from "@/components/screens/functions/functions-list";
import { modelEndpointsPageVariables, MODELS_LIST } from "@/components/screens/models/models-list";
import { toolDefsPageVariables, TOOLS_LIST } from "@/components/screens/agents/tools/tools-list";
import {
  workloadsPageVariables,
  WORKLOADS_LIST,
} from "@/components/screens/workloads/workloads-list";

import { agentsCrumbs, numberedPageVariables, selectPage, splitErrors } from "./catalog";
import { skillToolsPageVariables } from "./skill-tools-list";
import { SKILLS_LIST, skillsPageVariables } from "./skills-list";

/** A URL query as the numbered list state a hook reads. */
function queryOf(def: ListDefinition, qs: string) {
  const state = parseListState(def, qs);
  return {
    q: state.q,
    filters: effectiveFilters(def, state),
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  };
}

const base = { filters: {}, q: "", sort: [], page: 1, pageSize: 25 };

describe("agentsCrumbs", () => {
  it("starts at the Agents switcher with the function checked", () => {
    const [area, self] = agentsCrumbs("skills");
    expect(area.label).toBe("Agents");
    expect(area.switcher?.find((o) => o.active)?.href).toBe("/agents/skills");
    expect(self).toEqual({ label: "Skills" });
  });

  it("links the function once an entity follows it", () => {
    expect(agentsCrumbs("models", { label: "qwen" }).slice(1)).toEqual([
      { label: "Models", href: "/models" },
      { label: "qwen" },
    ]);
  });
});

describe("selectPage", () => {
  const spec = {
    matches: () => true,
    text: (n: { id: string }) => [n.id],
    sortValue: { id: (n: { id: string }) => n.id },
    id: (n: { id: string }) => n.id,
  };
  const rows = ["c", "a", "b", "d"].map((id) => ({ id }));

  it("sorts, then slices one page, and counts the whole match", () => {
    const out = selectPage(rows, spec, {
      ...base,
      sort: [{ key: "id", dir: "desc" }],
      page: 2,
      pageSize: 3,
    });
    expect(out.rows.map((r) => r.id)).toEqual(["a"]);
    expect(out.totalCount).toBe(4);
  });

  it("searches case-insensitively and breaks ties on the id", () => {
    expect(selectPage(rows, spec, { ...base, q: " B " }).rows).toEqual([{ id: "b" }]);
    expect(selectPage(rows, spec, base).rows.map((r) => r.id)).toEqual(["a", "b", "c", "d"]);
  });
});

describe("splitErrors", () => {
  it("puts each error beside its field, across snake and camel case", () => {
    expect(
      splitErrors(
        [
          { field: "handler_ref", message: "Not a URL." },
          { field: "slug", message: "Taken." },
          { field: "slug", message: "Too long." },
          { field: "orgId", message: "No access." },
        ],
        ["slug", "handlerRef"] as const
      )
    ).toEqual({ handlerRef: "Not a URL.", slug: "Taken. Too long.", form: "No access." });
  });
});

describe("numberedPageVariables", () => {
  it("trims search, nulls an empty filter and falls back to the default sort", () => {
    expect(
      numberedPageVariables({ ...base, q: "  qwen " }, {}, [{ key: "name", dir: "asc" }])
    ).toEqual({ search: "qwen", filter: null, sort: "name", page: 1, pageSize: 25 });
    expect(
      numberedPageVariables(
        { ...base, page: 0, sort: [{ key: "created", dir: "desc" }] },
        {
          a: 1,
        },
        []
      )
    ).toMatchObject({ filter: { a: 1 }, sort: "-created", page: 1 });
  });
});

describe("the catalog lists, as server variables", () => {
  it("Skills: Mine is created by me, Imported is imported, chips map to filter fields", () => {
    expect(skillsPageVariables(queryOf(SKILLS_LIST, "")).filter).toBeNull();
    expect(skillsPageVariables(queryOf(SKILLS_LIST, "")).sort).toBe("-updated");
    expect(skillsPageVariables(queryOf(SKILLS_LIST, "view=mine")).filter).toEqual({
      createdBy: ["me"],
    });
    expect(skillsPageVariables(queryOf(SKILLS_LIST, "view=imported")).filter).toEqual({
      imported: true,
    });
    expect(
      skillsPageVariables(queryOf(SKILLS_LIST, "status=inactive&scope=global&page=3")).filter
    ).toEqual({ scope: ["global"], active: false });
  });

  it("Tools: Built-in and Custom split on the flag; skill and adapter chips", () => {
    expect(toolDefsPageVariables(queryOf(TOOLS_LIST, "view=builtin")).filter).toEqual({
      builtin: true,
    });
    expect(toolDefsPageVariables(queryOf(TOOLS_LIST, "view=custom")).filter).toEqual({
      builtin: false,
    });
    expect(toolDefsPageVariables(queryOf(TOOLS_LIST, "view=mine")).filter).toEqual({
      createdBy: ["me"],
    });
    expect(
      toolDefsPageVariables(queryOf(TOOLS_LIST, "skill=crm&adapter=python_fn&sort=skill,name"))
    ).toMatchObject({ filter: { skill: ["crm"], adapter: ["python_fn"] }, sort: "skill,name" });
  });

  it("A skill's Tools tab: that skill, in its own scope", () => {
    expect(
      skillToolsPageVariables({ slug: "crm", isGlobal: false }, { ...base, filters: {} }).filter
    ).toEqual({ skill: ["crm"], scope: ["org"] });
    expect(
      skillToolsPageVariables(
        { slug: "repo-search", isGlobal: true },
        { ...base, filters: { adapter: "mcp_server" } }
      ).filter
    ).toEqual({ skill: ["repo-search"], scope: ["global"], adapter: ["mcp_server"] });
  });

  it("Models: Endpoints and Hosted are variant lists; a Serving chip narrows them", () => {
    expect(modelEndpointsPageVariables(queryOf(MODELS_LIST, "view=hosted"))?.filter).toEqual({
      variant: ["vllm", "kserve"],
    });
    expect(modelEndpointsPageVariables(queryOf(MODELS_LIST, "view=endpoints"))?.filter).toEqual({
      variant: ["bedrock", "vertex_ai", "azure_openai", "azure_foundry"],
    });
    expect(
      modelEndpointsPageVariables(queryOf(MODELS_LIST, "view=hosted&serving=VLLM"))?.filter
    ).toEqual({ variant: ["vllm"] });
    expect(
      modelEndpointsPageVariables(
        queryOf(MODELS_LIST, "view=mine&owner=project&status=failed&cluster=aws-main")
      )?.filter
    ).toEqual({
      deployedBy: ["me"],
      ownerScope: ["project"],
      status: ["failed"],
      cluster: ["aws-main"],
    });
  });

  it("Models: a Serving chip outside its view asks nothing", () => {
    expect(modelEndpointsPageVariables(queryOf(MODELS_LIST, "view=endpoints&serving=vllm"))).toBe(
      null
    );
  });

  it("Workloads keep the area's kinds; Functions only functions", () => {
    expect(workloadsPageVariables(queryOf(WORKLOADS_LIST, ""))).toEqual({
      kinds: ["agent", "workflow", "function"],
      search: null,
      filter: null,
      sort: "name",
      page: 1,
      pageSize: 25,
    });
    expect(
      workloadsPageVariables(queryOf(WORKLOADS_LIST, "view=agents&app=support")).filter
    ).toEqual({
      kind: ["agent"],
      app: ["support"],
    });
    expect(workloadsPageVariables(queryOf(WORKLOADS_LIST, "view=mine")).filter).toEqual({
      owner: ["me"],
    });
    expect(functionsPageVariables(queryOf(FUNCTIONS_LIST, "view=mine"))).toMatchObject({
      kinds: ["function"],
      filter: { owner: ["me"] },
    });
  });
});
