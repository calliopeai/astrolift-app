import { describe, expect, it } from "vitest";

import { DEFINITION, RUNS } from "./workflow-detail-a.fixtures";
import { DISABLED_WORKFLOW, RUN_FAILED, RUN_RUNNING, WORKFLOW } from "./workflow-detail-b.fixtures";
import {
  configuredSubject,
  definitionSubject,
  isFailedState,
  patternLabel,
  workflowStatus,
} from "./workflow-frame-subject";

describe("configuredSubject", () => {
  it("takes the newest run as the last run, and stages and project from the definition", () => {
    const subject = configuredSubject(WORKFLOW, DEFINITION);
    expect(subject.kind).toBe("configured");
    expect(subject.lastRun?.guid).toBe(RUN_RUNNING.guid);
    expect(subject.lastRun?.live).toBe(true);
    expect(subject.stageCount).toBe(DEFINITION.stageCount);
    expect(subject.projectSlug).toBe("sales-agents");
  });

  it("has no stage count before the definition loads", () => {
    expect(configuredSubject(WORKFLOW, null).stageCount).toBeNull();
  });
});

describe("definitionSubject", () => {
  it("picks this definition's newest run", () => {
    const subject = definitionSubject(DEFINITION, [...RUNS].reverse());
    expect(subject.lastRun?.guid).toBe(RUNS[0].guid);
    expect(subject.lastRun?.live).toBe(true);
  });

  it("ignores other definitions' runs", () => {
    const other = RUNS.map((r) => ({ ...r, definitionGuid: "someone-else" }));
    expect(definitionSubject(DEFINITION, other).lastRun).toBeNull();
  });

  it("settles an expired definition with an unknown result", () => {
    const subject = definitionSubject(DEFINITION, [{ ...RUNS[0]!, status: "expired" }]);
    expect(subject.lastRun?.live).toBe(false);
    expect(workflowStatus(subject)).toEqual({ dot: "muted", label: "Last run history expired" });
  });
});

describe("workflowStatus", () => {
  const base = configuredSubject(WORKFLOW, null);
  it("reads running, then disabled, then never run, then how the last run ended", () => {
    expect(workflowStatus(base).label).toBe("Running");
    expect(workflowStatus(configuredSubject(DISABLED_WORKFLOW, null)).label).toBe("Disabled");
    expect(workflowStatus({ ...base, lastRun: null }).label).toBe("Never run");
    expect(workflowStatus(configuredSubject({ ...WORKFLOW, runs: [RUN_FAILED] }, null))).toEqual({
      dot: "error",
      label: "Last run failed",
    });
    const ended = (state: string) =>
      workflowStatus({ ...base, lastRun: { ...base.lastRun!, state, live: false } }).label;
    expect(ended("cancelled")).toBe("Last run cancelled");
    expect(ended("completed")).toBe("Last run succeeded");
    expect(ended("expired")).toBe("Last run history expired");
  });
});

describe("helpers", () => {
  it("knows the failed spellings", () => {
    expect(["failed", "FAILED", "error", "timed_out"].every(isFailedState)).toBe(true);
    expect(["completed", "running", "cancelled"].some(isFailedState)).toBe(false);
  });

  it("labels a pattern kind as words", () => {
    expect(patternLabel("fan_out_aggregate")).toBe("Fan out aggregate");
    expect(patternLabel("")).toBe("Pipeline");
  });
});
