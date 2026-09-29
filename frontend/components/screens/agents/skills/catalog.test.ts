import { describe, expect, it } from "vitest";

import { selectModels } from "@/components/screens/models/models-list";
import { MODELS } from "@/components/screens/models/models-providers.fixtures";
import { selectTools } from "@/components/screens/agents/tools/tools-list";
import { TOOLS as REGISTRY_TOOLS } from "@/components/screens/agents/tools/agent-tools.fixtures";
import { selectFunctions } from "@/components/screens/functions/functions-list";
import {
  APP_WORKLOAD,
  AREA,
  MANY_WORKLOADS,
} from "@/components/screens/workloads/workloads.fixtures";
import { areaWorkloads, selectWorkloads } from "@/components/screens/workloads/workloads-list";

import { MANY_SKILLS, SKILL_ITEMS } from "./agent-skills.fixtures";
import { agentsCrumbs, selectPage, splitErrors } from "./catalog";
import { selectSkills } from "./skills-list";

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

describe("the catalog lists", () => {
  it("Skills: Mine is the org's own, Imported cannot pick any out yet", () => {
    expect(selectSkills(SKILL_ITEMS, { ...base, filters: { scope: "org" } }).totalCount).toBe(2);
    expect(selectSkills(SKILL_ITEMS, { ...base, filters: { imported: "1" } }).totalCount).toBe(0);
    expect(selectSkills(MANY_SKILLS, { ...base, page: 3 }).rows).toHaveLength(10);
  });

  it("Tools: Built-in and Custom stay empty until the flag is in the API", () => {
    expect(selectTools(REGISTRY_TOOLS, { ...base, filters: { builtin: "1" } }).totalCount).toBe(0);
    expect(
      selectTools(REGISTRY_TOOLS, { ...base, filters: { adapter: "python_fn" } }).totalCount
    ).toBe(2);
  });

  it("Models: Endpoints are cloud-served, Hosted run on the org's GPUs", () => {
    const names = (hosted: string) =>
      selectModels(MODELS, {
        ...base,
        sort: [{ key: "name", dir: "asc" }],
        filters: { hosted },
      }).rows.map((m) => m.name);
    expect(names("1")).toEqual(["embeddings", "llama-70b", "qwen"]);
    expect(names("0")).toEqual(["claude"]);
  });

  it("Workloads keep only the area's kinds; Functions only functions", () => {
    expect(areaWorkloads([APP_WORKLOAD, ...AREA])).toEqual(AREA);
    expect(selectWorkloads(AREA, { ...base, filters: { kind: "agent" } }).totalCount).toBe(1);
    expect(selectFunctions(MANY_WORKLOADS, base).totalCount).toBe(30);
  });
});
