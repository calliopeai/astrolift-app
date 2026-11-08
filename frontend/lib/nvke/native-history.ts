import type {
  GetNvkeNativeHistoryQuery,
  GetNvkeNativeHistoryQueryVariables,
} from "@/graphql/__generated__/operations";
import { validNvkeGuid } from "./recovery";
export type NvkeNativeHistoryPage = NonNullable<
  GetNvkeNativeHistoryQuery["astroliftNvkeNativeHistory"]["data"]
>;
export type NvkeNativeHistoryEvent = NvkeNativeHistoryPage["items"][number];
export const exactNativeEventId = (value: unknown): value is string =>
  typeof value === "string" &&
  /^[1-9][0-9]{0,18}$/.test(value) &&
  (value.length < 19 || value <= "9223372036854775807");
const before = (left: string, right: string) =>
  left.length < right.length || (left.length === right.length && left < right);
export function reviewedNativeHistory(
  reply: GetNvkeNativeHistoryQuery["astroliftNvkeNativeHistory"] | null | undefined,
  review: GetNvkeNativeHistoryQueryVariables & {
    clusterId: string;
    workflowId: string;
    runId?: string;
  }
): NvkeNativeHistoryPage | null {
  const page = reply?.data,
    limit = review.limit ?? 50;
  if (
    !reply?.ok ||
    reply.errors.length ||
    !page ||
    !Number.isSafeInteger(limit) ||
    limit < 1 ||
    limit > 200 ||
    ![
      review.expectedSiteVersion,
      review.expectedAuthorityVersion,
      review.expectedSourceVersion,
      review.expectedActorId,
    ].every((value) => Number.isSafeInteger(value) && value > 0) ||
    !validNvkeGuid(page.siteId) ||
    !validNvkeGuid(page.sourceOperationId) ||
    !validNvkeGuid(page.clusterId) ||
    !validNvkeGuid(page.runId) ||
    page.siteId !== review.siteId ||
    page.siteVersion !== review.expectedSiteVersion ||
    page.authorityVersion !== review.expectedAuthorityVersion ||
    page.sourceOperationId !== review.sourceOperationId ||
    page.sourceVersion !== review.expectedSourceVersion ||
    page.clusterId !== review.clusterId ||
    page.workflow !== review.workflow ||
    page.workflowId !== review.workflowId ||
    !page.workflowId ||
    (review.runId !== undefined && page.runId !== review.runId) ||
    !["RenewK8sCertsV2", "RotateEtcdEncryptionKey"].includes(page.workflow) ||
    ![
      "RUNNING",
      "COMPLETED",
      "FAILED",
      "CANCELED",
      "TERMINATED",
      "TIMED_OUT",
      "CONTINUED_AS_NEW",
    ].includes(page.status) ||
    !page.observedAt ||
    !Number.isFinite(Date.parse(page.observedAt)) ||
    !Array.isArray(page.items) ||
    page.items.length > limit ||
    (page.nextCursor != null &&
      (typeof page.nextCursor !== "string" || !page.nextCursor || page.nextCursor.length > 65536))
  )
    return null;
  let previous: string | null = null;
  for (const item of page.items) {
    if (
      !exactNativeEventId(item.eventId) ||
      (previous !== null && !before(previous, item.eventId)) ||
      !item.eventType
    )
      return null;
    previous = item.eventId;
    for (const id of [item.scheduledEventId, item.workflowTaskCompletedEventId])
      if (id != null && (!exactNativeEventId(id) || !before(id, item.eventId))) return null;
  }
  return page;
}
