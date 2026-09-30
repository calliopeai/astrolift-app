"use client";

import { InfoIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftPolicyConditionKind } from "@/graphql/__generated__/schema";
import { cn } from "@/lib/utils";

/** The catalog entry of a condition kind, as far as the help reads it. */
export type ConditionCatalogEntry = Pick<
  AstroliftPolicyConditionKind,
  "kind" | "label" | "description" | "needs"
>;

export interface PolicyConditionHelpProps {
  /** `astroliftPolicyConditionCatalog.conditions`: the server's own table. */
  catalog: ConditionCatalogEntry[];
  /** The kinds the draft uses, in its order; a kind the catalog lacks is said to be unknown. */
  kinds: string[];
  loading?: boolean;
  error?: { message: string } | null;
  className?: string;
}

/**
 * What each `needs` means for a check. A condition whose attribute the
 * request does not carry cannot be answered, and the policy then denies.
 */
export const NEEDS_TEXT: Record<string, string> = {
  clock: "Read from the server's clock: every check can answer it.",
  client_ip:
    "Needs the caller's address. A check made without one (a background job, an internal call) cannot answer it, so it denies.",
  session:
    "Needs the caller's own sign-in session. A token or a check made on someone else's behalf has none, so it denies.",
  operation:
    "Needs the operation to say it (an approval, the target environment). A check that does not carry it cannot answer, so it denies.",
};

/**
 * Beside the rule (design 3.6): each condition the draft uses, from the
 * server's catalog (`astroliftPolicyConditionCatalog`), with what a check
 * must carry to answer it. The resolver enforces policies, and a condition
 * the request cannot answer denies, so this is where that is said. Pure.
 */
export function PolicyConditionHelp({
  catalog,
  kinds,
  loading = false,
  error,
  className,
}: PolicyConditionHelpProps) {
  const t = useTranslations("shared.access.conditionHelp");
  const used = [...new Set(kinds)];
  if (used.length === 0) return null;
  if (loading) {
    return (
      <div className={cn("flex min-w-0 flex-col gap-2", className)} aria-busy>
        <Skeleton className="h-4 w-1/2" />
        <Skeleton className="h-10" />
      </div>
    );
  }
  const byKind = new Map(catalog.map((c) => [c.kind, c]));
  return (
    <section aria-label={t("label")} className={cn("flex min-w-0 flex-col gap-2", className)}>
      <p className="text-muted-foreground flex min-w-0 items-start gap-2 text-xs">
        <InfoIcon aria-hidden className="mt-0.5 size-3.5 shrink-0" />
        <span className="min-w-0">{t("intro")}</span>
      </p>
      {error && (
        <p role="alert" className="text-warning-fg text-xs [overflow-wrap:anywhere]">
          {t("failed", { message: error.message })}
        </p>
      )}
      <dl className="flex min-w-0 flex-col gap-2">
        {used.map((kind) => {
          const entry = byKind.get(kind);
          return (
            <div key={kind} className="min-w-0">
              <dt className="text-sm font-medium [overflow-wrap:anywhere]">
                {entry?.label ?? kind}
                <span className="text-muted-foreground ml-2 font-mono text-xs">{kind}</span>
              </dt>
              <dd className="text-muted-foreground min-w-0 text-xs [overflow-wrap:anywhere]">
                {entry
                  ? `${entry.description} ${Object.prototype.hasOwnProperty.call(NEEDS_TEXT, entry.needs) ? t(`needs.${entry.needs}`) : t("unknownNeeds", { needs: entry.needs })}`
                  : error
                    ? t("pending")
                    : t("unknown")}
              </dd>
            </div>
          );
        })}
      </dl>
    </section>
  );
}
