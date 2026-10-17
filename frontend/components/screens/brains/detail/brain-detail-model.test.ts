import { describe, expect, it } from "vitest";

import {
  DEFAULT_ASK,
  environmentNames,
  gateAsk,
  historyEntry,
  hostedAppRows,
  pickEnvironment,
  shareHistoryFilter,
  shareLevel,
} from "./brain-detail-model";
import { AUDIT_ROWS, BRAIN, NOT_SERVED } from "./brain-detail.fixtures";
import {
  activeBrainSection,
  brainHref,
  brainSectionHref,
  brainSections,
  brainTabs,
  resolveBrainTab,
} from "./brain-tabs-model";

interface SafetyRow {
  action: string;
  enabled: boolean;
  confirmation: boolean;
  verification: boolean;
  approval: boolean;
  minimumApprovals: number;
}

const row = (patch: Partial<SafetyRow>): SafetyRow => ({
  action: "brain.share",
  enabled: true,
  confirmation: true,
  verification: true,
  approval: false,
  minimumApprovals: 0,
  ...patch,
});

describe("what an action-safety gate asks", () => {
  it("is on by default: confirmation and a fresh sign-in", () => {
    expect(gateAsk(null, "brain.share")).toEqual(DEFAULT_ASK);
    expect(gateAsk([row({ action: "app.deregister" })], "brain.delete")).toEqual(DEFAULT_ASK);
  });

  it("follows the rule the organization set, approval included", () => {
    expect(
      gateAsk(
        [row({ action: "brain.delete", approval: true, minimumApprovals: 2 })],
        "brain.delete"
      )
    ).toEqual({
      confirmation: true,
      verification: true,
      approval: true,
      minimumApprovals: 2,
    });
    expect(gateAsk([row({ enabled: false })], "brain.share")).toEqual({
      confirmation: false,
      verification: false,
      approval: false,
      minimumApprovals: 0,
    });
  });
});

describe("shares and hosted apps", () => {
  it("reads access levels, owner apart and unknown ones as nothing", () => {
    expect(shareLevel("viewer")).toBe("viewer");
    expect(shareLevel("deployer")).toBe("deployer");
    expect(shareLevel("owner")).toBe("owner");
    expect(shareLevel("auditor")).toBeNull();
  });

  it("gives one row per hosted app with its environments together", () => {
    expect(hostedAppRows(BRAIN.hostedApps ?? [])).toEqual([
      { slug: "brain-chat", environments: ["production", "staging"] },
      { slug: "support-bot", environments: ["production"] },
    ]);
    expect(hostedAppRows([{ slug: "undeployed", environment: "" }])).toEqual([
      { slug: "undeployed", environments: [] },
    ]);
  });
});

describe("environments", () => {
  it("are the served ones, or production before anything is served", () => {
    expect(environmentNames(BRAIN)).toEqual(["production", "staging"]);
    expect(environmentNames(NOT_SERVED)).toEqual(["production"]);
  });

  it("follow ?env= when the layer has it, else production", () => {
    expect(pickEnvironment(BRAIN, "staging")).toBe("staging");
    expect(pickEnvironment(BRAIN, "qa")).toBe("production");
    expect(pickEnvironment({ environments: [{ name: "staging", url: "x" }] }, null)).toBe(
      "staging"
    );
  });
});

describe("the access history", () => {
  it("asks the audit trail for this layer's shares and unshares only", () => {
    expect(shareHistoryFilter("platform-brain")).toMatchObject({
      action: ["brain.share", "brain.unshare"],
      targetKind: ["RegisteredApp"],
      targetId: "platform-brain",
    });
  });

  it("reads each row: who, what, the outcome and the shares after", () => {
    const [shared, refused, agent] = AUDIT_ROWS.map(historyEntry);
    expect(shared).toMatchObject({
      kind: "share",
      outcome: "ok",
      actor: "ana@acme.example",
      sharedWith: [
        { team: "medops", access: "viewer" },
        { team: "sales", access: "deployer" },
      ],
    });
    expect(refused).toMatchObject({
      outcome: "refused",
      code: "STEP_UP_REQUIRED",
      sharedWith: null,
    });
    expect(agent).toMatchObject({ kind: "unshare", surface: "mcp" });
  });

  it("calls a checked call that did not apply failed, and survives odd data", () => {
    expect(
      historyEntry({
        ...AUDIT_ROWS[0]!,
        data: { error_code: "VALIDATION", shared_with: [{ team: 1 }] },
      })
    ).toMatchObject({ outcome: "failed", code: "VALIDATION", sharedWith: [] });
    expect(historyEntry({ ...AUDIT_ROWS[0]!, data: "not an object" })).toMatchObject({
      outcome: "ok",
      sharedWith: null,
      surface: null,
    });
  });
});

describe("the tab row", () => {
  it("puts Overview at the root and each other tab on its own route", () => {
    expect(brainHref("platform-brain")).toBe("/brains/platform-brain");
    expect(brainHref("platform-brain", "sharing")).toBe("/brains/platform-brain/sharing");
    expect(resolveBrainTab("/brains/platform-brain/settings", "platform-brain")).toBe("settings");
    expect(resolveBrainTab("/brains/platform-brain", "platform-brain")).toBe("overview");
    const tabs = brainTabs("platform-brain", "/brains/platform-brain/apps", (k) => k.toUpperCase());
    expect(tabs.map((t) => [t.label, t.active])).toEqual([
      ["OVERVIEW", false],
      ["SUBSTRATE", false],
      ["SHARING", false],
      ["APPS", true],
      ["CONNECT", false],
      ["SETTINGS", false],
    ]);
  });

  it("picks a tab's section by ?section=, the first being the default", () => {
    expect(activeBrainSection("sharing", {})).toBe("teams");
    expect(activeBrainSection("sharing", { section: "history" })).toBe("history");
    expect(activeBrainSection("settings", { section: "danger-zone" })).toBe("danger-zone");
    expect(brainSectionHref("platform-brain", "settings", "danger-zone")).toBe(
      "/brains/platform-brain/settings?section=danger-zone"
    );
    expect(brainSectionHref("platform-brain", "settings", "rebuild")).toBe(
      "/brains/platform-brain/settings"
    );
    expect(brainSections("platform-brain", "sharing", (k) => k).map((s) => s.id)).toEqual([
      "teams",
      "history",
    ]);
  });
});
