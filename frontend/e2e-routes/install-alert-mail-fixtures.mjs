import { GraphQLError } from "graphql";
export const alertMailWrites = [];
let rows = [];
export function resetAlertMail() {
  rows = [];
  alertMailWrites.length = 0;
}
export function alertMailFixture(field, args, role, parent) {
  if (
    !["installAlertMailSupport", "installAlertMailTestsPage", "sendInstallAlertMailTest"].includes(
      field
    )
  )
    return undefined;
  if (role !== "owner") throw new GraphQLError("Current operator required");
  if (field === "installAlertMailSupport")
    return {
      allowed: true,
      reason: null,
      transport: "smtp",
      sender: "alerts@install.example",
      recipient: "operator@example.test",
      tlsMode: "starttls",
      sourceFingerprint: "a".repeat(64),
      checkedAt: "2026-10-04T12:00:00Z",
    };
  if (field === "installAlertMailTestsPage")
    return {
      items: rows.filter((r) => r.eventKind === args.eventKind),
      totalCount: rows.length,
      nextCursor: null,
    };
  if (parent === "Mutation" && field === "sendInstallAlertMailTest") {
    const input = args.input;
    if (
      input.expectedSourceFingerprint !== "a".repeat(64) ||
      !input.requestId ||
      input.eventKind !== "deploy.failed"
    )
      throw new GraphQLError("Reviewed current intent required");
    alertMailWrites.push(input);
    const row = {
      id: "30000000-0000-4000-8000-000000000001",
      requestId: input.requestId,
      version: 2,
      eventKind: input.eventKind,
      transport: "smtp",
      sender: "alerts@install.example",
      recipient: "operator@example.test",
      status: "accepted",
      reasonCode: null,
      createdAt: "2026-10-04T12:00:00Z",
      acceptedAt: "2026-10-04T12:00:01Z",
      deliveryObserved: false,
    };
    rows = [row];
    return { ok: true, errors: [], data: row };
  }
  return undefined;
}
