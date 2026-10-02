"use client";
import { KeyIcon, ShieldCheckIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import { ListSummary } from "@/components/list/ListSummary";
import type { useSecretProposalsSummary } from "./use-secret-proposals-summary";
export type SecretProposalsSummaryProps = ReturnType<typeof useSecretProposalsSummary>;
export function SecretProposalsSummary(props: SecretProposalsSummaryProps) {
  const t = useTranslations("lists.secretProposalsQueue");
  return (
    <ListSummary
      {...props}
      rows={props.error ? [] : props.rows}
      title={t("title")}
      description={t("paging.description")}
      icon={<KeyIcon className="size-4" />}
      keyOf={(row) => row.id}
      rowHref={(row) => `/approvals/secret/${row.id}`}
      viewAllHref="/approvals/secret"
      renderRow={(row) => (
        <span
          className="block truncate font-mono"
          title={`${row.registeredAppSlug} / ${row.environmentName}`}
        >
          {row.registeredAppSlug} → {row.environmentName || t("row.appWide")}
        </span>
      )}
      empty={{
        title: t("empty.title"),
        description: t("empty.description"),
        icon: <ShieldCheckIcon className="size-6" />,
      }}
    />
  );
}
