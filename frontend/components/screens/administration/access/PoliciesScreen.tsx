"use client";

import { BookOpenIcon, InfoIcon, PlusIcon, ScaleIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PageShell } from "@/components/PageShell";
import { DataTable, type Column } from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AstroliftPolicy } from "@/graphql/identity/identity.types";
import { DOC_LINKS } from "@/lib/docs/urls";
import { useFormatters } from "@/lib/i18n/formatters";

import { CreatePolicySheet } from "./CreatePolicySheet";
import type { usePolicies } from "./use-policies";

const effectStyles: Record<string, string> = {
  ALLOW: "bg-success/15 text-success-fg",
  DENY: "bg-danger/15 text-danger-fg",
};

export type PoliciesScreenProps = ReturnType<typeof usePolicies>;

/** Administration > Policies: the ABAC policy list with create and soft-delete. */
export function PoliciesScreen({
  table,
  deleting,
  deletePolicy,
  creating,
  createPolicy,
}: PoliciesScreenProps) {
  const [open, setOpen] = React.useState(false);
  const fmt = useFormatters();

  const [deleteTarget, setDeleteTarget] = React.useState<AstroliftPolicy | null>(null);

  // The create sheet refetches LIST_POLICIES, which is a different root field
  // from the page this table walks, so a newly created policy would not appear
  // until a navigation. Refetch the walk when the sheet closes.
  function handleCreateOpenChange(next: boolean) {
    setOpen(next);
    if (!next) table.refetch();
  }

  const columns: Column<AstroliftPolicy>[] = [
    {
      id: "policy",
      header: "Policy",
      cell: (p) => (
        <>
          <div className="font-medium">{p.name}</div>
          <div className="text-muted-foreground font-mono text-xs">{p.slug}</div>
        </>
      ),
    },
    {
      id: "scope",
      header: "Scope",
      cell: (p) => <Badge variant="outline">{p.scopeLevel}</Badge>,
    },
    {
      id: "effect",
      header: "Effect",
      cell: (p) => (
        <Badge className={effectStyles[p.effect]} variant="secondary">
          {p.effect}
        </Badge>
      ),
    },
    {
      id: "action",
      header: "Action",
      cellClassName: "font-mono text-xs",
      cell: (p) => p.actionPattern,
    },
    {
      id: "conditions",
      header: "Conditions",
      cellClassName: "text-muted-foreground text-xs",
      cell: (p) =>
        Array.isArray(p.conditions) && p.conditions.length > 0
          ? `${p.conditions.length} condition${p.conditions.length === 1 ? "" : "s"}`
          : "—",
    },
    {
      id: "createdBy",
      header: "Created by",
      cellClassName: "text-muted-foreground text-xs",
      cell: (p) => p.createdByUsername ?? "—",
    },
    {
      id: "createdAt",
      header: "Created at",
      cellClassName: "text-muted-foreground text-xs",
      cell: (p) => (p.createdAt ? fmt.formatDate(p.createdAt) : "—"),
    },
    {
      id: "actions",
      header: "Actions",
      align: "right",
      cell: (p) => (
        <Can permission="org.update">
          <Button size="sm" variant="ghost" onClick={() => setDeleteTarget(p)} disabled={deleting}>
            <Trash2Icon className="size-4" />
            <span className="sr-only">Delete</span>
          </Button>
        </Can>
      ),
    },
  ];

  return (
    <PageShell
      title="ABAC policies"
      description="Runtime predicates evaluated after RBAC. Policies can only deny — they never grant beyond role bindings."
      actions={
        <>
          <Button asChild size="sm" variant="outline">
            <Link href={DOC_LINKS.policies}>
              <BookOpenIcon className="size-4" />
              Learn more
            </Link>
          </Button>
          <Can permission="org.update">
            <Button onClick={() => setOpen(true)}>
              <PlusIcon className="size-4" />
              New policy
            </Button>
          </Can>
        </>
      }
    >
      <div className="border-info-border bg-info/10 flex flex-col gap-2 rounded-md border p-3">
        <div className="flex items-center gap-2">
          <InfoIcon className="text-info-fg size-4" />
          <span className="text-sm font-medium">What ABAC policies do</span>
        </div>
        <p className="text-muted-foreground text-xs leading-relaxed">
          Attribute-Based Access Control (ABAC) grants or denies an action by evaluating attributes
          of the user, the resource, and the request context — such as time of day, source IP,
          environment, or MFA freshness — against policy rules. Policies here run{" "}
          <span className="font-medium">after</span> role-based access (RBAC) and can only deny:
          they narrow what a member&apos;s roles already allow, never widen it. Use this page to
          create and review those rules.
        </p>
      </div>

      <DataTable
        label="Policies"
        controller={table}
        columns={columns}
        getRowId={(p) => p.id}
        searchPlaceholder="Search policies…"
        // DENY rows get a flush left border in destructive tone so the
        // danger-zone read is obvious at a glance — a DENY can lock operators
        // out even when role bindings would otherwise permit the call.
        rowClassName={(p) =>
          p.effect === "DENY" ? "border-l-destructive/70 border-l-2" : undefined
        }
        empty={{
          icon: <ScaleIcon className="size-5" />,
          title: "No policies yet",
          description:
            "ABAC policies layer on top of RBAC. They can deny based on time, IP, env, MFA freshness — never grant beyond what the role binding already permits.",
        }}
        emptyFiltered={{
          title: "No matching policies",
          description:
            "No policy matches that search. The server matches name, slug, description and action pattern — scope and effect are not searchable.",
        }}
      />

      <CreatePolicySheet
        open={open}
        onOpenChange={handleCreateOpenChange}
        onCreate={createPolicy}
        creating={creating}
      />

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
    </PageShell>
  );
}
