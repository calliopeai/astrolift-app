import { describe, expect, it } from "vitest";

import {
  accessEditorHref,
  edgeAccessVariables,
  fromEdgeAccessRow,
  fromPersonPage,
  NOT_ENFORCED,
} from "./edge-access-list";

const base = { filters: {}, q: "", pageSize: 25, after: null };

const serverRow = {
  id: "a:e",
  appSlug: "conflict bdr",
  appName: "Conflict BDR",
  teamSlug: "sales",
  projectSlug: "bdr",
  environment: "prod",
  environmentId: "e",
  clusterSlug: "conflict-astrolift",
  clusterName: "CONFLICT",
  hosts: ["bdr.apps.conflict.cloud"],
  restricted: true,
  groups: ["conflict-sales"],
  users: [],
  source: "ui",
  setBy: "leo@calliope.ai",
  setAt: "2026-10-26T13:58:40Z",
  status: "PENDING_DEPLOY" as const,
  edgeChangedAt: "2026-10-26T13:58:40Z",
  edgeAppliedAt: null,
};

describe("app access list variables (#2534)", () => {
  it("sends nothing it was not asked for", () => {
    expect(edgeAccessVariables(base)).toEqual({
      filter: null,
      search: null,
      first: 25,
      after: null,
    });
  });

  it("sends team, project and cluster as they are, and status in the schema's case", () => {
    expect(
      edgeAccessVariables({
        ...base,
        filters: { team: "sales", project: "bdr", cluster: "c1", status: "pending_deploy" },
        q: "  staff ",
        after: "cur",
      })
    ).toEqual({
      filter: { team: "sales", project: "bdr", cluster: "c1", status: ["PENDING_DEPLOY"] },
      search: "staff",
      first: 25,
      after: "cur",
    });
  });

  it("asks for every not-enforced status under the Not enforced view", () => {
    expect(edgeAccessVariables({ ...base, filters: { status: "attention" } }).filter).toEqual({
      status: NOT_ENFORCED.map((s) => s.toUpperCase()),
    });
  });

  it("never sends a status the server does not know", () => {
    expect(edgeAccessVariables({ ...base, filters: { status: "bogus" } }).filter).toEqual({
      status: [],
    });
  });

  it("leaves the person out of the filter: it picks the query", () => {
    expect(edgeAccessVariables({ ...base, filters: { person: "me" } }).filter).toBeNull();
  });
});

describe("app access rows", () => {
  it("open the app's own access editor, on its edge section", () => {
    expect(accessEditorHref("conflict bdr")).toBe("/apps/conflict%20bdr/access?section=edge");
    const row = fromEdgeAccessRow(serverRow);
    expect(row.href).toBe("/apps/conflict%20bdr/access?section=edge");
    expect(row.status).toBe("pending_deploy");
    expect(row.entry).toBeUndefined();
  });

  it("carry how a person gets in, and the logins behind it", () => {
    const { rows, person } = fromPersonPage({
      email: "eric@calliope.ai",
      logins: [
        {
          clusterSlug: "conflict-astrolift",
          resolved: true,
          reason: "",
          hasLogin: true,
          enabled: true,
          groups: ["conflict-sales"],
        },
      ],
      items: [
        { enters: true, via: "group", viaGroups: ["conflict-sales"], rule: serverRow },
        { enters: null, via: "surprise", viaGroups: [], rule: serverRow },
      ],
      nextCursor: null,
      totalCount: 2,
      refusedCount: 3,
    });
    expect(rows.map((r) => r.entry)).toEqual([
      { enters: true, via: "group", groups: ["conflict-sales"] },
      { enters: null, via: "unknown", groups: [] },
    ]);
    expect(person.refusedCount).toBe(3);
    expect(person.logins[0]!.groups).toEqual(["conflict-sales"]);
  });
});
