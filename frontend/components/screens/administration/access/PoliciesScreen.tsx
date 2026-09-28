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
import type { AstroliftPolicy } from "@/graphql/identity/identity.types";
import { DOC_LINKS } from "@/lib/docs/urls";
import { useFormatters } from "@/lib/i18n/formatters";

import { adminCrumb } from "./admin-crumbs";
import type { usePolicies } from "./use-policies";

const effectStyles: Record<string, string> = {
  ALLOW: "bg-success/15 text-success-fg",
  DENY: "bg-danger/15 text-danger-fg",
};

export type PoliciesScreenProps = ReturnType<typeof usePolicies>;

/** Admin › Policies: the ABAC policy list (spec 44 §5.1) with soft-delete. */
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
      cellClassName: "max-w-80",
      cell: (p) => (
        <div className="min-w-0">
          <div className="truncate font-medium" title={p.name}>
            {p.name}
          </div>
          <div className="text-muted-foreground truncate font-mono text-xs" title={p.slug}>
            {p.slug}
          </div>
        </div>
      ),
    },
    {
      id: "scope",
      header: "Scope",
      cell: (p) => (
        <Badge variant="outline" className="font-mono">
          {p.scopeLevel}
        </Badge>
      ),
    },
    {
      id: "effect",
      header: "Effect",
      cell: (p) => (
        <Badge className={`${effectStyles[p.effect] ?? ""} font-mono`} variant="secondary">
          {p.effect}
        </Badge>
      ),
    },
    {
      id: "action",
      header: "Action",
      cellClassName: "max-w-64",
      cell: (p) => (
        <span className="block min-w-0 truncate font-mono text-xs" title={p.actionPattern}>
          {p.actionPattern}
        </span>
      ),
    },
    {
      id: "conditions",
      header: "Conditions",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (p) =>
        Array.isArray(p.conditions) && p.conditions.length > 0
          ? `${p.conditions.length} condition${p.conditions.length === 1 ? "" : "s"}`
          : "—",
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
          {p.createdByUsername ?? "—"}
        </span>
      ),
    },
    {
      id: "createdAt",
      header: "Created at",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (p) => (p.createdAt ? fmt.formatDate(p.createdAt) : "—"),
    },
  ];

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6 p-6">
      <ListPage<AstroliftPolicy>
        header={{
          crumbs: [adminCrumb("policies"), { label: "Policies" }],
          title: "ABAC policies",
          context: "Evaluated after RBAC. Policies can only deny.",
          primaryAction: (
            <Can permission="org.update">
              <Button size="sm" asChild>
                <Link href="/administration/policies/new">
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
            "ABAC policies layer on top of RBAC. They can deny based on time, IP, env, MFA freshness — never grant beyond what the role binding already permits.",
          actionHref: canManage ? "/administration/policies/new" : undefined,
          actionLabel: canManage ? "New policy" : undefined,
          learnMoreHref: DOC_LINKS.policies,
        }}
      />

      <div className="border-info-border bg-info/10 flex min-w-0 flex-col gap-2 rounded-md border p-3">
        <div className="flex items-center gap-2">
          <InfoIcon className="text-info-fg size-4 shrink-0" />
          <span className="text-sm font-medium">What ABAC policies do</span>
        </div>
        <p className="text-muted-foreground text-xs leading-relaxed">
          Attribute-Based Access Control (ABAC) grants or denies an action by evaluating attributes
          of the user, the resource, and the request context — such as time of day, source IP,
          environment, or MFA freshness — against policy rules. Policies here run{" "}
          <span className="font-medium">after</span> role-based access (RBAC) and can only deny:
          they narrow what a member&apos;s roles already allow, never widen it. The server matches
          name, slug, description and action pattern in search; scope and effect are not searchable.{" "}
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
        description="Soft-deletes the policy. Any DENY rule it enforced stops applying immediately — operators previously blocked by this policy regain that access on their next request. The slug becomes reclaimable for a new policy."
        confirmLabel="Delete policy"
        destructive
        onConfirm={async () => {
          if (deleteTarget) await deletePolicy(deleteTarget);
        }}
      />
    </div>
  );
}
