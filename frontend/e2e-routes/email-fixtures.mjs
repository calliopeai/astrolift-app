// Controlled schema-validated mail metadata for the actual production frontend.
// No native sender, external mail or DNS transport is invoked.
export const emailWrites = [];
let records = [];
const serviceId = "40000000-0000-4000-8000-000000000001";
const app = {
  id: "b0000000-0000-4000-8000-000000000001",
  name: "Controlled mail app",
  slug: "mail-app",
  organizationSlug: "fixture",
  version: 1,
};
const service = {
  id: serviceId,
  name: "Controlled SES mail",
  kind: "email",
  variant: "ses",
  status: "active",
  environmentName: "production",
  registeredAppSlug: app.slug,
};
export function resetEmailFixtures() {
  emailWrites.length = 0;
  records = [];
}
export function emailFixture(field, args, role, parent, operationName) {
  if (!operationName?.startsWith("EmailDelivery") && operationName !== "SendEmailDeliveryTest")
    return undefined;
  if (field === "astroliftAppsPage") return { items: [app], nextCursor: null, totalCount: 1 };
  if (field === "astroliftApp") return args.slug === app.slug ? app : null;
  if (field === "astroliftManagedServicesPage")
    return {
      items: args.appSlug === app.slug ? [service] : [],
      nextCursor: null,
      totalCount: args.appSlug === app.slug ? 1 : 0,
    };
  if (field === "emailDeliveryTestSupport")
    return {
      allowed: role === "owner",
      reason: role === "owner" ? null : "MANAGED_SERVICE_UPDATE_REQUIRED",
      serviceVersion: 7,
      sender: "sender@acme.example",
      identity: "acme.example",
      accountId: "123456789012",
      region: "us-west-2",
    };
  if (field === "emailDeliveryTestsPage")
    return { items: records, nextCursor: null, totalCount: records.length };
  if (parent === "Mutation" && field === "sendEmailDeliveryTest") {
    const input = args.input;
    if (role !== "owner" || input.managedServiceId !== serviceId || input.expectedVersion !== 7)
      throw new Error("Controlled reviewed mail target refused");
    emailWrites.push({ ...input });
    let row = records.find((record) => record.requestId === input.requestId);
    if (!row) {
      row = {
        id: "30000000-0000-4000-8000-000000000001",
        managedServiceId: serviceId,
        version: 2,
        requestId: input.requestId,
        sender: "sender@acme.example",
        recipient: input.recipient,
        status: "accepted",
        accountId: "123456789012",
        region: "us-west-2",
        identity: "acme.example",
        transport: "aws_ses",
        providerMessageId: "controlled-message",
        eventTrackingConfigured: true,
        simulator: input.recipient.endsWith("@simulator.amazonses.com"),
        createdAt: "2026-10-04T12:00:00Z",
        acceptedAt: "2026-10-04T12:00:01Z",
        observedAt: null,
        reasonCode: null,
      };
      records.unshift(row);
    }
    return { ok: true, errors: [], data: row };
  }
  return undefined;
}
