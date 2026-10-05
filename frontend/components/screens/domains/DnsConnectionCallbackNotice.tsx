"use client";
import { useTranslations } from "next-intl";

export function DnsConnectionCallbackNotice({
  outcome,
}: {
  outcome?: "saved" | "unconfirmed" | "denied";
}) {
  const t = useTranslations("domainConnections");
  if (!outcome) return null;
  return (
    <p role="status" className="px-6 pt-4">
      {t(
        { saved: "callbackSaved", unconfirmed: "callbackUnconfirmed", denied: "callbackDenied" }[
          outcome
        ]
      )}
    </p>
  );
}
