import { expect, it } from "vitest";
import { INSTALL_REVIEW_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallReviewPanel.stories";
import {
  installReviewSchema,
  installWorkloadSchema,
  installWorkloadPageSchema,
} from "./operator-install-review";
const review = INSTALL_REVIEW_PROPS.review!;
const row = INSTALL_REVIEW_PROPS.table.rows[0];
it("preserves decimal counters beyond JS precision and unknown observations", () => {
  expect(installWorkloadSchema.parse(row).generation).toBe("9007199254740993");
  expect(installWorkloadSchema.parse(INSTALL_REVIEW_PROPS.table.rows[1]).readyReplicas).toBeNull();
});
it.each(["01", "-1", "1.0", "9223372036854775808", "1e3"])(
  "rejects noncanonical or overflowing counters %s",
  (generation) => {
    expect(installWorkloadSchema.safeParse({ ...row, generation }).success).toBe(false);
  }
);
it.each([
  { observedGeneration: null },
  { observedGeneration: "1" },
  { desiredReplicas: "0", readyReplicas: "0", updatedReplicas: "0" },
  { readyReplicas: null },
  { updatedReplicas: "0" },
  { readyReplicas: "2" },
])("rejects a false ready claim %j", (fields) => {
  expect(installWorkloadSchema.safeParse({ ...row, ...fields }).success).toBe(false);
});
it.each([
  { continuationAuthorized: true },
  { nativeExitCode: 0 },
  { apiReady: false },
  { workloadCount: 0 },
  { snapshotExpiresAt: review.observedAt },
  { originalStartedAt: review.snapshotExpiresAt },
])("rejects malformed review %j", (fields) => {
  expect(installReviewSchema.safeParse({ ...review, ...fields }).success).toBe(false);
});
it("projects metadata and discards raw diagnostics", () => {
  const parsed = installReviewSchema.parse({ ...review, rawLogs: "private", intent: "opaque" });
  expect(parsed).toEqual(review);
});
it("rejects duplicate workload identities and impossible pages", () => {
  const page = {
    snapshotId: review.snapshotId,
    operationId: review.operationId,
    operationVersion: review.operationVersion,
    reviewDigest: review.reviewDigest,
    items: [row],
    totalCount: 1,
    nextCursor: null,
  };
  expect(installWorkloadPageSchema.safeParse(page).success).toBe(true);
  expect(
    installWorkloadPageSchema.safeParse({ ...page, items: [row, row], totalCount: 2 }).success
  ).toBe(false);
  expect(installWorkloadPageSchema.safeParse({ ...page, totalCount: 0 }).success).toBe(false);
  expect(
    installWorkloadPageSchema.safeParse({ ...page, items: [], nextCursor: "cursor" }).success
  ).toBe(false);
});
