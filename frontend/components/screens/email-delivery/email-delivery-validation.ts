import type { DeliveryDraft } from "./EmailDeliveryPanel";

/** Match the native single-mailbox/content limits before committing any replay intent. */
export function deliveryValidation(
  draft: DeliveryDraft
): "invalidRecipient" | "invalidContent" | null {
  const recipient = draft.recipient.trim();
  const mailbox = /^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$/;
  const [local, domain] = recipient.split("@");
  if (
    recipient.length > 254 ||
    !mailbox.test(recipient) ||
    local.length > 64 ||
    local.includes("..") ||
    domain.includes("..") ||
    !domain.includes(".")
  )
    return "invalidRecipient";
  // TextEncoder replaces isolated surrogates; reject them before hashing or GraphQL serialization.
  for (const value of [draft.subject, draft.body]) {
    for (let i = 0; i < value.length; i++) {
      const unit = value.charCodeAt(i);
      if (unit >= 0xd800 && unit <= 0xdbff) {
        const next = value.charCodeAt(++i);
        if (!(next >= 0xdc00 && next <= 0xdfff)) return "invalidContent";
      } else if (unit >= 0xdc00 && unit <= 0xdfff) return "invalidContent";
    }
  }
  const subject = draft.subject.trim(),
    body = draft.body.trim();
  if (
    (subject &&
      (new TextEncoder().encode(subject).length > 200 || /[\x00-\x1f\x7f]/.test(subject))) ||
    (body && new TextEncoder().encode(body).length > 8192)
  )
    return "invalidContent";
  return null;
}
