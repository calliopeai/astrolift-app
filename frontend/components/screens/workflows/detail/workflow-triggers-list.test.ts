import { describe, expect, it } from "vitest";

import { effectiveFilters, parseListState } from "@/components/list/list-state";

import { DISABLED_WORKFLOW, WORKFLOW } from "./workflow-detail-b.fixtures";
import {
  selectTriggers,
  type TriggerRow,
  triggerRows,
  WORKFLOW_TRIGGERS_LIST,
} from "./workflow-triggers-list";

const WEBHOOK: TriggerRow = {
  id: "nightly-sync:webhook",
  kind: "webhook",
  schedule: null,
  isEnabled: false,
};

function select(rows: TriggerRow[], query = "") {
  const state = parseListState(WORKFLOW_TRIGGERS_LIST, query);
  return selectTriggers(rows, {
    filters: effectiveFilters(WORKFLOW_TRIGGERS_LIST, state),
    q: state.q,
    sort: state.sort,
    page: state.page,
    pageSize: state.pageSize,
  });
}

describe("workflow triggers list", () => {
  it("reads a configured workflow's one trigger as one row, and none for a definition", () => {
    expect(triggerRows(WORKFLOW)).toEqual([
      { id: "nightly-sync:schedule", kind: "schedule", schedule: "0 4 * * *", isEnabled: true },
    ]);
    expect(triggerRows(DISABLED_WORKFLOW)[0]).toMatchObject({ kind: "manual", isEnabled: false });
    expect(triggerRows(null)).toEqual([]);
  });

  it("leads with All and Mine, and All says the list holds one trigger today", () => {
    expect(WORKFLOW_TRIGGERS_LIST.views.map((v) => v.key)).toEqual([
      "all",
      "mine",
      "enabled",
      "disabled",
    ]);
    expect(WORKFLOW_TRIGGERS_LIST.views[0].note).toMatch(/one trigger today/);
  });

  it("filters by view, kind chip and search", () => {
    const rows = [...triggerRows(WORKFLOW), WEBHOOK];
    expect(select(rows).totalCount).toBe(2);
    expect(select(rows, "view=enabled").rows.map((r) => r.kind)).toEqual(["schedule"]);
    expect(select(rows, "view=disabled").rows.map((r) => r.kind)).toEqual(["webhook"]);
    expect(select(rows, "kind=webhook").rows.map((r) => r.kind)).toEqual(["webhook"]);
    expect(select(rows, "q=0%204").rows.map((r) => r.kind)).toEqual(["schedule"]);
    // Triggers record no creator yet.
    expect(select(rows, "view=mine").totalCount).toBe(0);
  });

  it("sorts by kind and status, and pages", () => {
    const rows = [...triggerRows(WORKFLOW), WEBHOOK];
    expect(select(rows).rows.map((r) => r.kind)).toEqual(["schedule", "webhook"]);
    expect(select(rows, "sort=-kind").rows.map((r) => r.kind)).toEqual(["webhook", "schedule"]);
    expect(select(rows, "sort=status").rows.map((r) => r.kind)).toEqual(["webhook", "schedule"]);
    const many = Array.from({ length: 30 }, (_, i) => ({ ...WEBHOOK, id: `w${i}` }));
    expect(select(many).rows).toHaveLength(25);
    expect(select(many, "page=2")).toMatchObject({ totalCount: 30 });
    expect(select(many, "page=2").rows).toHaveLength(5);
  });
});
