import { describe, expect, it } from "vitest";

import { type ClientListSpec, selectClientRows } from "./client-list";
import { secretsSection } from "./secrets/secrets-list";
import { metricsPanel, metricsPanelQuery } from "./tools/metrics-panels";

interface Row {
  id: string;
  name: string;
  kind: string;
}

const SPEC: ClientListSpec<Row> = {
  matches: (r, key, value) => (key === "kind" ? r.kind === value : true),
  text: (r) => [r.name],
  compare: (a, b, key) => (key === "name" ? a.name.localeCompare(b.name) : 0),
  tie: (a, b) => a.id.localeCompare(b.id),
};

const ROWS: Row[] = Array.from({ length: 30 }, (_, i) => ({
  id: `r-${String(i).padStart(2, "0")}`,
  name: `row ${String(29 - i).padStart(2, "0")}`,
  kind: i % 3 === 0 ? "cron" : "web",
}));

const STATE = { q: "", sort: [{ key: "name", dir: "asc" as const }], page: 1, pageSize: 25 };

describe("selectClientRows", () => {
  it("sorts the whole set before it pages, and counts every match", () => {
    const { rows, totalCount } = selectClientRows(ROWS, {}, STATE, SPEC);
    expect(totalCount).toBe(30);
    expect(rows).toHaveLength(25);
    expect(rows[0]!.name).toBe("row 00");
    const second = selectClientRows(ROWS, {}, { ...STATE, page: 2 }, SPEC);
    expect(second.rows.map((r) => r.name)).toEqual([
      "row 25",
      "row 26",
      "row 27",
      "row 28",
      "row 29",
    ]);
  });

  it("filters and searches before it counts", () => {
    expect(selectClientRows(ROWS, { kind: "cron" }, STATE, SPEC).totalCount).toBe(10);
    expect(selectClientRows(ROWS, {}, { ...STATE, q: "ROW 1" }, SPEC).totalCount).toBe(10);
  });

  it("reverses on a descending sort", () => {
    const { rows } = selectClientRows(
      ROWS,
      {},
      { ...STATE, sort: [{ key: "name", dir: "desc" }] },
      SPEC
    );
    expect(rows[0]!.name).toBe("row 29");
  });
});

describe("section and panel params", () => {
  it("reads the Secrets section, defaulting to the keys", () => {
    expect(secretsSection("bundles")).toBe("bundles");
    expect(secretsSection(undefined)).toBe("keys");
    expect(secretsSection("nope")).toBe("keys");
  });

  it("reads the Metrics panel, defaulting to Signals", () => {
    expect(metricsPanel("pods")).toBe("pods");
    expect(metricsPanel(null)).toBe("signals");
    expect(metricsPanel(["alerts"])).toBe("signals");
  });

  it("keeps the section and the metric scope when it switches panel, and drops the picks", () => {
    const qs = metricsPanelQuery(
      "section=metrics&panel=pods&pod=web-1&env=prod&rule=r-1",
      "alerts"
    );
    expect(new URLSearchParams(qs).toString()).toBe("section=metrics&panel=alerts&env=prod");
    expect(metricsPanelQuery("section=metrics&panel=pods", "signals")).toBe("section=metrics");
  });
});
