"use client";

import { useQuery } from "@apollo/client/react";
import { KeyIcon, ShieldCheckIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { LIST_SECRET_CHANGE_PROPOSALS } from "@/graphql/services/services.queries";
import type { AstroliftSecretChangeProposal } from "@/graphql/services/services.types";
import { useFormatters } from "@/lib/i18n/formatters";

interface ListResp {
  astroliftSecretChangeProposals: AstroliftSecretChangeProposal[];
}

/**
 * Secret-change proposals queue (#488). Renders below the deployment
 * approvals queue on the global /approvals page — distinct list so the
 * two axes' per-row affordances (env scope, approver policy) stay
 * separate. Clicking a row routes to the proposal-detail page where
 * the operator approves / rejects / inspects the diff.
 */
export function SecretProposalsQueueClient() {
  const t = useTranslations("lists.secretProposalsQueue");
  const fmt = useFormatters();

  const { data, loading } = useQuery<ListResp>(LIST_SECRET_CHANGE_PROPOSALS, {
    variables: { appSlug: null, status: "pending" },
    fetchPolicy: "cache-and-network",
    pollInterval: 30_000,
  });

  const proposals = data?.astroliftSecretChangeProposals ?? [];

  if (loading && !data) {
    return (
      <section className="mt-8">
        <h2 className="mb-3 text-lg font-semibold">{t("title")}</h2>
        <Skeleton className="h-24 w-full" />
      </section>
    );
  }

  if (proposals.length === 0) {
    return (
      <section className="mt-8">
        <h2 className="mb-3 text-lg font-semibold">{t("title")}</h2>
        <EmptyState
          title={t("empty.title")}
          description={t("empty.description")}
          icon={<ShieldCheckIcon className="size-6" aria-hidden />}
        />
      </section>
    );
  }

  return (
    <section className="mt-8">
      <h2 className="mb-3 text-lg font-semibold">{t("title")}</h2>
      <ul className="flex flex-col gap-2">
        {proposals.map((proposal) => (
          <li key={proposal.id}>
            <ProposalRow proposal={proposal} formatRelative={fmt.formatRelativeTime} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function ProposalRow({
  proposal,
  formatRelative,
}: {
  proposal: AstroliftSecretChangeProposal;
  formatRelative: (d: Date | string, now?: Date) => string;
}) {
  const t = useTranslations("lists.secretProposalsQueue.row");
  const env = proposal.environmentName || t("appWide");
  const summary =
    (proposal.payloadDiff as { summary?: string })?.summary ?? `${proposal.op} on ${env}`;
  return (
    <div className="bg-background flex flex-col gap-2 rounded-md border p-3 sm:flex-row sm:items-center">
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <KeyIcon className="text-muted-foreground size-4 shrink-0" aria-hidden />
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex flex-wrap items-center gap-2">
            <Link href={`/approvals/secret/${proposal.id}`} className="font-medium hover:underline">
              {proposal.registeredAppSlug} → {env}
            </Link>
            <Badge variant="outline" className="text-2xs">
              {t("typeBadge")}
            </Badge>
            <Badge variant="outline" className="text-2xs">
              {t("opLabel", { op: proposal.op })}
            </Badge>
            <Badge variant="outline" className="text-2xs">
              {t("approvalsCount", {
                received: proposal.approvalsCount,
                required: proposal.requiredApproverCount,
              })}
            </Badge>
          </div>
          <div className="text-muted-foreground text-xs">
            {t("summary", { summary })}
            {proposal.proposerDisplayName &&
              ` · ${t("proposer", { name: proposal.proposerDisplayName })}`}
            {` · ${t("received", { rel: formatRelative(proposal.createdAt) })}`}
          </div>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Button asChild size="sm" variant="outline" className="min-h-11 w-full sm:w-auto">
          <Link href={`/approvals/secret/${proposal.id}`}>{t("review")}</Link>
        </Button>
      </div>
    </div>
  );
}
