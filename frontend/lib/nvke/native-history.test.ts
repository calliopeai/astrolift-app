import { expect, it } from "vitest";
import {
  exactNativeEventId,
  reviewedNativeHistory,
  type NvkeNativeHistoryPage,
} from "./native-history";
const id = "123e4567-e89b-12d3-a456-426614174001";
const review = {
  siteId: id,
  expectedSiteVersion: 7,
  expectedAuthorityVersion: 3,
  sourceOperationId: id,
  expectedSourceVersion: 9,
  expectedActorId: 41,
  workflow: "RotateEtcdEncryptionKey",
  clusterId: id,
  workflowId: "owner-更新",
  runId: id,
  limit: 25,
};
const page: NvkeNativeHistoryPage = {
  siteId: id,
  siteVersion: 7,
  authorityVersion: 3,
  sourceOperationId: id,
  sourceVersion: 9,
  sourceObservedAt: "2026-11-07T12:00:00Z",
  clusterId: id,
  workflow: "RotateEtcdEncryptionKey",
  workflowId: "owner-更新",
  runId: id,
  status: "FAILED",
  recordedState: "running",
  recordedPhase: "rewrite",
  retainedMutationLock: true,
  observedAt: "2026-11-07T12:00:01Z",
  items: [
    {
      eventId: "9007199254740993",
      eventType: "ACTIVITY_TASK_SCHEDULED",
      activityType: "StartEtcdEncryptionRewrite",
      occurredAt: null,
      scheduledEventId: null,
      workflowTaskCompletedEventId: "9007199254740992",
    },
  ],
  nextCursor: "BOUND_CURSOR",
};
it("keeps exact IDs and separates native status from recorded controller state", () => {
  expect(reviewedNativeHistory({ ok: true, errors: [], data: page }, review)).toEqual(page);
  expect(exactNativeEventId("9223372036854775807")).toBe(true);
});
it.each([
  0,
  1,
  9007199254740992,
  "0",
  "01",
  "1.0",
  "+1",
  "١",
  "9223372036854775808",
  "12345678901234567890",
  null,
])("refuses inexact or invalid native event %s", (value) =>
  expect(exactNativeEventId(value)).toBe(false)
);
it.each([
  "siteVersion",
  "authorityVersion",
  "sourceVersion",
  "siteId",
  "sourceOperationId",
  "clusterId",
  "runId",
  "workflowId",
  "workflow",
  "status",
  "observedAt",
])("refuses a mismatched native history %s", (field) => {
  const changed = {
    ...page,
    [field]: typeof page[field as keyof typeof page] === "number" ? 0 : "changed",
  };
  expect(reviewedNativeHistory({ ok: true, errors: [], data: changed }, review)).toBeNull();
});
it("refuses stale run pages even with the same workflow/cluster", () => {
  expect(
    reviewedNativeHistory(
      { ok: true, errors: [], data: page },
      { ...review, runId: "123e4567-e89b-12d3-a456-426614174002" }
    )
  ).toBeNull();
});
it.each(["event", "order", "link", "limit", "envelope"])("refuses invalid history %s", (kind) => {
  const changed = structuredClone(page);
  if (kind === "event") changed.items[0].eventId = "09007199254740993";
  if (kind === "order") changed.items.push({ ...changed.items[0] });
  if (kind === "link") changed.items[0].workflowTaskCompletedEventId = changed.items[0].eventId;
  expect(
    reviewedNativeHistory(
      { ok: kind !== "envelope", errors: [], data: changed },
      { ...review, limit: kind === "limit" ? 0 : 25 }
    )
  ).toBeNull();
});

it("orders decimal IDs numerically across different string widths", () => {
  const changed = {
    ...page,
    items: [
      { ...page.items[0], eventId: "9", workflowTaskCompletedEventId: "8" },
      { ...page.items[0], eventId: "10", workflowTaskCompletedEventId: "9" },
    ],
  };
  expect(reviewedNativeHistory({ ok: true, errors: [], data: changed }, review)).toEqual(changed);
  expect(
    reviewedNativeHistory(
      { ok: true, errors: [], data: { ...changed, items: [...changed.items].reverse() } },
      review
    )
  ).toBeNull();
});
