"use client";

import { InfoIcon, PlusIcon, ScaleIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { SCOPE_NOUN } from "@/components/access/access-model";
import { parsePolicy } from "@/components/access/policy-model";
import { PolicySentence } from "@/components/access/PolicySentence";
import type { AstroliftPolicy } from "@/graphql/identity/identity.types";
import { DOC_LINKS } from "@/lib/docs/urls";
import { useFormatters } from "@/lib/i18n/formatters";

import { NEW_POLICY_HREF, policiesCrumbs, policyHref } from "./policy-routes";
import type { usePolicies } from "./use-policies";

export type PoliciesScreenProps = ReturnType<typeof usePolicies>;

/**
 * Admin › Policies: the ABAC policy list (design 3.6, spec 44 §5.1), each row
 * read as its sentence and opening the policy's page, with soft-delete. The
 * backend stores policies and does not evaluate them yet (design 1), so
 * nothing here claims a policy denies anything today.
 */
export function PoliciesScreen({
  list,
  page,
  canManage,
  deleting,
  deletePolicy,
}: PoliciesScreenProps) {
  const fmt = useFormatters();
  const [deleteTarget, setDeleteTarget] = React.useState<AstroliftPolicy | null>(null);

  const columns: Column<AstroliftPolicy>[] = [
    {
      id: "policy",
      header: "Policy",
      cellClassName: "max-w-[36rem]",
      // The sentence is the row (design 3.6): what it denies, on what, for
      // whom and unless what, read the way the editor builds it.
      cell: (p) => (
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="flex min-w-0 items-baseline gap-2">
            <span className="truncate font-medium" title={p.name}>
              {p.name}
            </span>
            <span
              className="text-muted-foreground min-w-0 truncate font-mono text-xs"
              title={p.slug}
            >
              {p.slug}
            </span>
          </span>
          <PolicySentence policy={parsePolicy(p)} className="text-muted-foreground line-clamp-2" />
        </span>
      ),
    },
    {
      id: "scope",
      header: "Scope",
      cell: (p) => (
        <Badge variant="outline" className="font-mono">
          {SCOPE_NOUN[p.scopeLevel] ?? p.scopeLevel}
        </Badge>
      ),
    },
    {
      id: "createdBy",
      header: "Created by",
      cellClassName: "max-w-48",
      cell: (p) => (
        <span
          className="text-muted-foreground block min-w-0 truncate font-mono text-xs"
          title={p.createdByUsername ?? undefined}
        >
          {p.createdByUsername ?? "unknown"}
        </span>
      ),
    },
    {
      id: "updatedAt",
      header: "Updated",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (p) => fmt.formatDate(p.updatedAt || p.createdAt),
    },
  ];

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6 p-6">
      <ListPage<AstroliftPolicy>
        header={{
          crumbs: policiesCrumbs(),
          title: "Policies",
          context: "Rules that narrow what roles allow. Stored, not yet enforced.",
          primaryAction: (
            <Can permission="org.update">
              <Button size="sm" asChild>
                <Link href={NEW_POLICY_HREF}>
                  <PlusIcon className="size-4" />
                  New policy
                </Link>
              </Button>
            </Can>
          ),
        }}
        list={list}
        label="Policies"
        columns={columns}
        rows={page.rows}
        getRowId={(p) => p.id}
        rowHref={(p) => policyHref(p.id)}
        loading={page.loading}
        stale={page.stale}
        error={page.error}
        onRetry={page.refetch}
        totalCount={page.totalCount}
        nextCursor={page.nextCursor}
        // DENY rows get a flush left border in destructive tone so the
        // danger-zone read is obvious at a glance: a DENY can lock operators
        // out even when role bindings would otherwise permit the call.
        rowClassName={(p) =>
          p.effect === "DENY" ? "border-l-destructive/70 border-l-2" : undefined
        }
        rowActions={
          canManage
            ? (p) => (
                <DropdownMenuItem
                  variant="destructive"
                  disabled={deleting}
                  onSelect={() => setDeleteTarget(p)}
                >
                  <Trash2Icon className="size-4" />
                  Delete
                </DropdownMenuItem>
              )
            : undefined
        }
        empty={{
          icon: <ScaleIcon className="size-5" />,
          title: "No policies yet",
          description:
            "A policy narrows what roles allow: deny an action unless it is working hours, the office network, approved, or a fresh sign-in. It never grants more than a role does.",
          actionHref: canManage ? NEW_POLICY_HREF : undefined,
          actionLabel: canManage ? "New policy" : undefined,
          learnMoreHref: DOC_LINKS.policies,
        }}
      />

      <div className="border-warning-border bg-warning/10 flex min-w-0 items-start gap-2 rounded-md border p-3">
        <InfoIcon className="text-warning-fg mt-0.5 size-4 shrink-0" />
        <p className="text-muted-foreground min-w-0 text-xs leading-relaxed">
          <span className="text-foreground font-medium">Not enforced yet.</span> Astrolift stores
          these policies and reads them back as written, but the permission check does not evaluate
          them: today a policy changes no one&apos;s access. Search matches name, slug, description
          and action.{" "}
          <Link className="underline underline-offset-2" href={DOC_LINKS.policies}>
            Learn more
          </Link>
        </p>
      </div>

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
        title={deleteTarget ? `Delete policy ${deleteTarget.slug}?` : "Delete policy?"}
        description="Soft-deletes the policy and frees its slug for a new one. Policies are not enforced yet, so no one's access changes today; once they are, whatever this policy denied is allowed again."
        confirmLabel="Delete policy"
        destructive
        onConfirm={async () => {
          if (deleteTarget) await deletePolicy(deleteTarget);
        }}
      />
    </div>
  );
}
