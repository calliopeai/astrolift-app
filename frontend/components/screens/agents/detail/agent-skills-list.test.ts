import { describe, expect, it } from "vitest";

import { BUILD } from "./agent-build-run.fixtures";
import { selectSkills, selectTools, toolAdapters, toolRows } from "./agent-skills-list";

const byPosition = [{ key: "position", dir: "asc" as const }];

describe("selectSkills", () => {
  it("keeps binding order by default and counts every match", () => {
    const { rows, totalCount } = selectSkills(BUILD.skills, {}, "", byPosition, 1, 25);
    expect(totalCount).toBe(BUILD.skills.length);
    expect(rows.map((b) => b.position)).toEqual(
      [...BUILD.skills].map((b) => b.position).sort((a, b) => a - b)
    );
  });

  it("filters by status and search, and pages the result", () => {
    const inactive = selectSkills(BUILD.skills, { status: "inactive" }, "", byPosition, 1, 25);
    expect(inactive.rows.every((b) => !b.skill.isActive)).toBe(true);
    const first = BUILD.skills[0].skill;
    const hit = selectSkills(BUILD.skills, {}, first.slug.toUpperCase(), byPosition, 1, 25);
    expect(hit.rows.map((b) => b.skill.id)).toContain(first.id);
    const paged = selectSkills(BUILD.skills, {}, "", byPosition, 2, 1);
    expect(paged.rows).toHaveLength(1);
    expect(paged.totalCount).toBe(BUILD.skills.length);
  });
});

describe("selectTools", () => {
  const all = toolRows(BUILD.skills);

  it("flattens every skill's tools with the skill that carries them", () => {
    expect(all.length).toBe(BUILD.skills.reduce((n, b) => n + b.toolDefs.length, 0));
    expect(toolAdapters(all)).toEqual([...new Set(all.map((r) => r.tool.adapter))].sort());
  });

  it("filters by adapter and sorts descending on request", () => {
    const adapter = all[0].tool.adapter;
    const { rows } = selectTools(all, { adapter }, "", [{ key: "name", dir: "desc" }], 1, 25);
    expect(rows.every((r) => r.tool.adapter === adapter)).toBe(true);
    const names = rows.map((r) => r.tool.name);
    expect(names).toEqual([...names].sort((a, b) => b.localeCompare(a)));
  });
});
