import { describe, expect, it } from "vitest";

import {
  admitsReviewedSession,
  isReviewedExecTarget,
  type ReviewedExecTarget,
} from "./exec-session";

const reviewed: ReviewedExecTarget = {
  workloadId: "workload-guid",
  workloadVersion: 7,
  appId: "app-guid",
  appVersion: 9,
  environmentId: "environment-guid",
  environmentName: "staging",
  environmentVersion: 3,
  clusterId: "cluster-guid",
  clusterVersion: 4,
  namespace: "app-staging",
  podName: "web-1",
  podUid: "pod-incarnation-guid",
  container: "main",
  podBinding: "PREFLIGHT_ONLY",
  resumable: false,
};

describe("reviewed exec session admission", () => {
  it("admits the identical review and tolerates GraphQL's transport typename", () => {
    expect(admitsReviewedSession(reviewed, { ...reviewed })).toBe(true);
    expect(
      admitsReviewedSession(
        { ...reviewed, __typename: "AstroliftAppExecTarget" } as ReviewedExecTarget,
        reviewed
      )
    ).toBe(true);
  });

  it.each([
    "workloadId",
    "appId",
    "environmentId",
    "environmentName",
    "clusterId",
    "namespace",
    "podName",
    "podUid",
    "container",
  ] as const)("refuses a ready frame that retargets %s", (field) => {
    expect(admitsReviewedSession(reviewed, { ...reviewed, [field]: "different" })).toBe(false);
  });

  it.each(["workloadVersion", "appVersion", "environmentVersion", "clusterVersion"] as const)(
    "refuses a ready frame from a different %s",
    (field) => {
      expect(admitsReviewedSession(reviewed, { ...reviewed, [field]: reviewed[field] + 1 })).toBe(
        false
      );
    }
  );

  it.each([
    null,
    {},
    { ...reviewed, namespace: "" },
    { ...reviewed, workloadVersion: true },
    { ...reviewed, workloadVersion: -1 },
    { ...reviewed, clusterVersion: 1.5 },
    { ...reviewed, podBinding: "ATOMIC" },
    { ...reviewed, resumable: true },
  ])("refuses incomplete identity or unsupported authority: %j", (value) => {
    expect(isReviewedExecTarget(value)).toBe(false);
    expect(admitsReviewedSession(reviewed, value)).toBe(false);
  });
});
