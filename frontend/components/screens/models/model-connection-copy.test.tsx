import { parse, TYPE } from "@formatjs/icu-messageformat-parser";
import { render, screen } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider, useTranslations } from "next-intl";
import { describe, expect, it } from "vitest";
import { locales } from "@/i18n/config";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";

const catalogs = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
const keys = [
  "title",
  "add",
  "connections",
  "myRequests",
  "reviewInbox",
  "requestsDescription",
  "inboxDescription",
  "capabilityChecking",
  "unsupported",
  "capabilityFailed",
  "retry",
  "target",
  "action",
  "auto",
  "approval",
  "deny",
  "chooseTarget",
  "alias",
  "aliasHelp",
  "invalidAlias",
  "review",
  "reviewAuto",
  "reviewRequest",
  "reviewTarget",
  "requestNotice",
  "restartNotice",
  "requestApproval",
  "connect",
  "pending",
  "approved",
  "rejected",
  "cancelled",
  "stale",
  "unknown",
  "requestSaved",
  "approvedNotice",
  "connectionQueued",
  "uncertain",
  "refreshFailed",
  "changed",
  "requestFailed",
  "requestId",
  "model",
  "destination",
  "requester",
  "status",
  "votes",
  "createdAt",
  "approve",
  "reject",
  "cancelRequest",
  "reviewApprove",
  "reviewReject",
  "reviewCancel",
  "decisionNotice",
  "finalizeNotice",
  "decisionSaved",
  "emptyRequests",
  "emptyRequestsDescription",
  "openRequest",
  "backToRequests",
  "notFound",
  "policyTitle",
  "restrictionTitle",
  "policyDescription",
  "restrictionDescription",
  "mode",
  "quorum",
  "quorumHelp",
  "selfApproval",
  "savePolicy",
  "confirmPolicy",
  "reviewPolicy",
  "policyReviewNotice",
  "policySaved",
  "restrictionSaved",
  "policyUnavailable",
  "quorumInvalid",
  "recoveryAvailable",
  "restoreReview",
  "discardRecovery",
  "discardRecoveryNotice",
  "version",
  "requestKey",
  "viewRequests",
  "readOnlyEvidence",
  "connectionUncertain",
  "connectionRecorded",
  "acceptedUnverified",
] as const;
const englishContract = {
  title: "Model connections",
  add: "Add connection",
  connections: "Connections",
  myRequests: "Your requests",
  reviewInbox: "Review inbox",
  requestsDescription:
    "Requests in the selected organization. Approval does not create a connection.",
  inboxDescription: "Review only the requests the server currently admits for you.",
  capabilityChecking: "Checking connection approval support…",
  unsupported:
    "This server does not advertise connection approvals. New connections are unavailable here; existing connection observations remain readable.",
  capabilityFailed: "Connection approval support could not be checked. Retry before continuing.",
  retry: "Retry",
  target: "App environment",
  action: "Connection action",
  auto: "Connect automatically",
  approval: "Request approval",
  deny: "Denied",
  chooseTarget: "Choose a currently available app environment.",
  alias: "Connection alias",
  aliasHelp:
    "Use 1–32 lowercase letters, digits or underscores, beginning with a letter. Each alias has its own model binding.",
  invalidAlias: "Enter a valid connection alias.",
  review: "Review connection",
  reviewAuto: "Review direct connection",
  reviewRequest: "Review approval request",
  reviewTarget: "{model} → {target}, alias {alias}",
  requestNotice:
    "This records an approval request only. It creates no subscription, model credential or app connection.",
  restartNotice:
    "Connecting updates subscription credentials and restarts the model. Existing consumers may temporarily lose access; queued reconciliation is not readiness.",
  requestApproval: "Request approval",
  connect: "Connect",
  pending: "Pending approval",
  approved: "Approved",
  rejected: "Rejected",
  cancelled: "Cancelled",
  stale: "Stale",
  unknown: "Unknown",
  requestSaved: "Approval request recorded. No connection has been created.",
  approvedNotice:
    "Approval is recorded. The current requester must still choose Connect; the server rechecks the current target, policy and authority.",
  connectionQueued: "Connection accepted for reconciliation. It is not yet confirmed ready.",
  uncertain:
    "The reply was not confirmed. Keep this exact reviewed request and retry with the same request key, or inspect Your requests before creating another.",
  refreshFailed:
    "The write was accepted, but the follow-up read failed. Retry the read; do not treat it as a rejected write.",
  changed:
    "The actor, organization, target or reviewed source changed. Read the current state before continuing.",
  requestFailed: "The request was not accepted. Retry current reads before another attempt.",
  requestId: "Request ID",
  model: "Model",
  destination: "Destination",
  requester: "Requester",
  status: "Status",
  votes: "Current approvals: {count} of {required}",
  createdAt: "Created",
  approve: "Approve",
  reject: "Reject",
  cancelRequest: "Cancel request",
  reviewApprove: "Review approval",
  reviewReject: "Review rejection",
  reviewCancel: "Review cancellation",
  decisionNotice:
    "This changes the approval request only. It does not connect an app or promise model readiness.",
  finalizeNotice:
    "Connect this approved request using its current version. Existing consumers may temporarily lose access during credential reconciliation.",
  decisionSaved: "Request decision recorded. Read the current state before the next action.",
  emptyRequests: "No visible requests",
  emptyRequestsDescription:
    "Only the requests currently visible to you are included. A failed read is not an empty inventory.",
  openRequest: "Open request",
  backToRequests: "Back to requests",
  notFound: "This request is unavailable or no longer visible.",
  policyTitle: "Model connection policy",
  restrictionTitle: "Model connection restriction",
  policyDescription:
    "Organization defaults determine automatic connection, approval or denial. The server checks current destination authority and policy before every write.",
  restrictionDescription:
    "A platform super-admin can tighten this model’s organization policy. A neutral overlay does not replace organization defaults.",
  mode: "Connection policy",
  quorum: "Required distinct approvals",
  quorumHelp:
    "Choose 1–16 distinct eligible reviewers. The current server decides whether each vote still counts.",
  selfApproval: "Allow requester self-approval",
  savePolicy: "Review policy change",
  confirmPolicy: "Save policy",
  reviewPolicy: "Review model connection policy",
  policyReviewNotice:
    "This changes admission for future connection actions. It does not create or revoke an existing connection.",
  policySaved: "Connection policy saved. Existing connections are unchanged.",
  restrictionSaved: "Model restriction saved. It does not relax the organization policy.",
  policyUnavailable: "The current policy could not be read. Retry before editing.",
  quorumInvalid: "Enter an approval quorum from 1 to 16.",
  recoveryAvailable:
    "An unconfirmed request is retained for this actor and target. Restore its exact review to retry safely.",
  restoreReview: "Restore unconfirmed review",
  discardRecovery: "Discard local recovery",
  discardRecoveryNotice:
    "Discarding this local review does not cancel any request already accepted by the server. Inspect Your requests first.",
  version: "Reviewed version",
  requestKey: "Request key",
  viewRequests: "View your requests",
  readOnlyEvidence:
    "Read-time action decisions are advisory. The server rechecks authority when the action is submitted.",
  connectionUncertain:
    "The connection reply was not confirmed. Inspect current connections and deployment state before trying again.",
  connectionRecorded:
    "A subscription is recorded for this request. Inspect its current reconciliation and readiness.",
  acceptedUnverified:
    "The write was accepted, but its returned target metadata could not be verified. Read the current state before another action.",
};
const review = { model: "small-model <literal>", target: "app/staging & literal", alias: "chat_2" };
const quorum = { count: 2, required: 3 };

function ConnectionCopy() {
  const t = useTranslations("models.shared.connections");
  return (
    <section>
      <h1>{t("title")}</h1>
      <p data-testid="review-target">{t("reviewTarget", review)}</p>
      <p data-testid="current-votes">{t("votes", quorum)}</p>
      <p>{t("requestNotice")}</p>
      <p>{t("requestSaved")}</p>
      <p>{t("approvedNotice")}</p>
      <button type="button">{t("connect")}</button>
      <p>{t("connectionUncertain")}</p>
      <p>{t("connectionRecorded")}</p>
      <p>{t("acceptedUnverified")}</p>
      <p>{t("refreshFailed")}</p>
      <p>{t("discardRecoveryNotice")}</p>
      <h2>{t("policyTitle")}</h2>
      <label>
        <input type="checkbox" />
        {t("selfApproval")}
      </label>
      <p>{t("policyReviewNotice")}</p>
    </section>
  );
}

describe("model connection intake, review and recovery copy", () => {
  it("binds the exact reviewed English 89-key contract", () => {
    expect(en.models.shared.connections).toEqual(englishContract);
  });
  it.each(locales)(
    "%s has exact keys, genuine translations and only the reviewed ICU args",
    (locale) => {
      const copy = catalogs[locale].models.shared.connections;
      expect(Object.keys(copy).sort()).toEqual([...keys].sort());
      const t = createTranslator({
        locale,
        messages: copy,
        onError: (error) => {
          throw error;
        },
      });
      for (const key of keys) {
        expect(copy[key].trim(), key).not.toBe("");
        if (locale !== "en") expect(copy[key], key).not.toBe(en.models.shared.connections[key]);
        const found = parse(copy[key]).flatMap((node) => {
          if (node.type === TYPE.literal) return [];
          expect(node.type, `${locale}.${key}`).toBe(TYPE.argument);
          return "value" in node ? [node.value] : [];
        });
        const expected =
          key === "reviewTarget"
            ? ["model", "target", "alias"]
            : key === "votes"
              ? ["count", "required"]
              : [];
        expect(found.sort(), `${locale}.${key}`).toEqual(expected.sort());
        const formatted = t(key, { ...review, ...quorum });
        expect(formatted).not.toMatch(/\{(?:model|target|alias|count|required)\}/);
        expect(formatted).not.toBe(`models.shared.connections.${key}`);
        if (!expected.length) expect(formatted).toBe(copy[key]);
      }
      for (const value of Object.values(review)) expect(t("reviewTarget", review)).toContain(value);
      expect(t("votes", quorum)).toContain("2");
      expect(t("votes", quorum)).toContain("3");
    }
  );
  it.each(locales)(
    "%s renders distinct pending, approved, connection and uncertain-write outcomes",
    (locale) => {
      const errors: Error[] = [];
      const copy = catalogs[locale].models.shared.connections;
      const t = createTranslator({ locale, messages: copy });
      const { container } = render(
        <NextIntlClientProvider
          locale={locale}
          messages={catalogs[locale]}
          onError={(error) => errors.push(error)}
        >
          <ConnectionCopy />
        </NextIntlClientProvider>
      );
      expect(screen.getByRole("heading", { name: copy.title })).toBeInTheDocument();
      expect(screen.getByTestId("review-target")).toHaveTextContent(t("reviewTarget", review));
      expect(screen.getByTestId("current-votes")).toHaveTextContent(t("votes", quorum));
      for (const key of [
        "requestNotice",
        "requestSaved",
        "approvedNotice",
        "connectionUncertain",
        "connectionRecorded",
        "refreshFailed",
        "discardRecoveryNotice",
        "policyReviewNotice",
      ] as const) {
        expect(screen.getByText(copy[key])).toBeInTheDocument();
      }
      expect(screen.getByRole("button", { name: copy.connect })).toBeInTheDocument();
      expect(screen.getByRole("checkbox", { name: copy.selfApproval })).toBeInTheDocument();
      expect(container.querySelector("literal")).toBeNull();
      expect(errors).toEqual([]);
    }
  );
});
