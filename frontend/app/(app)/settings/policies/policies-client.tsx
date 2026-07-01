"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { BookOpenIcon, PlusIcon, ScaleIcon, Trash2Icon } from "lucide-react";
import { ListControls } from "@/components/ListControls";
import { useListControls } from "@/hooks/use-list-controls";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { CREATE_POLICY, SOFT_DELETE_POLICY } from "@/graphql/identity/identity.mutations";
import { LIST_POLICIES } from "@/graphql/identity/identity.queries";
import { DOC_LINKS } from "@/lib/docs/urls";
import type { AstroliftPolicy, MutationResult } from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { CreatePolicyDialog } from "./create-policy-dialog";

interface Resp {
  astroliftPolicies: AstroliftPolicy[];
}

const effectStyles: Record<string, string> = {
  ALLOW: "bg-success/15 text-success-fg",
  DENY: "bg-danger/15 text-danger-fg",
};

export function PoliciesClient() {
  const [open, setOpen] = React.useState(false);
  const policies = useQuery<Resp>(LIST_POLICIES);
  const fmt = useFormatters();

  const [softDelete, { loading: deleting }] = useMutation<{
    softDeletePolicy: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_POLICY, {
    refetchQueries: [{ query: LIST_POLICIES }],
    awaitRefetchQueries: true,
  });

  const [deleteTarget, setDeleteTarget] = React.useState<AstroliftPolicy | null>(null);

  async function handleDelete(p: AstroliftPolicy) {
    const { data } = await softDelete({ variables: { input: { id: p.id } } });
    if (data?.softDeletePolicy.ok) {
      toast.success(`Deleted ${p.slug}`);
    } else {
      throw new Error(data?.softDeletePolicy.errors?.[0]?.message ?? "Delete failed");
    }
  }

  const allPolicies = policies.data?.astroliftPolicies ?? [];
  const ctrl = useListControls({
    data: allPolicies,
    searchFn: (p) => [p.name, p.scopeLevel, p.effect, p.description].join(" "),
    initialPageSize: 25,
    sortFn: (a, b, s) => {
      const dir = s.dir === "asc" ? 1 : -1;
      if (s.key === "name") return a.name.localeCompare(b.name) * dir;
      if (s.key === "scope") return a.scopeLevel.localeCompare(b.scopeLevel) * dir;
      return 0;
    },
  });
  const list = ctrl.rows;

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
          <Button onClick={() => setOpen(true)}>
            <PlusIcon className="size-4" />
            New policy
          </Button>
        </>
      }
    >
      {allPolicies.length > 0 && (
        <ListControls controls={ctrl} searchPlaceholder="Search policies…" className="mb-3" />
      )}
      <Card>
        <CardContent className="p-0">
          {policies.loading ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<ScaleIcon className="size-5" />}
                title="No policies yet"
                description="ABAC policies layer on top of RBAC. They can deny based on time, IP, env, MFA freshness — never grant beyond what the role binding already permits."
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Policy</TableHead>
                  <TableHead>Scope</TableHead>
                  <TableHead>Effect</TableHead>
                  <TableHead>Action</TableHead>
                  <TableHead>Conditions</TableHead>
                  <TableHead>Created by</TableHead>
                  <TableHead>Created at</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((p) => {
                  const isDeny = p.effect === "DENY";
                  // DENY rows get a flush left border in destructive
                  // tone so the danger-zone read is obvious at a
                  // glance — a DENY can lock operators out even when
                  // role bindings would otherwise permit the call.
                  return (
                    <TableRow
                      key={p.id}
                      className={isDeny ? "border-l-destructive/70 border-l-2" : undefined}
                    >
                      <TableCell>
                        <div className="font-medium">{p.name}</div>
                        <div className="text-muted-foreground font-mono text-xs">{p.slug}</div>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">{p.scopeLevel}</Badge>
                      </TableCell>
                      <TableCell>
                        <Badge className={effectStyles[p.effect]} variant="secondary">
                          {p.effect}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs">{p.actionPattern}</TableCell>
                      <TableCell className="text-muted-foreground text-xs">
                        {Array.isArray(p.conditions) && p.conditions.length > 0
                          ? `${p.conditions.length} condition${p.conditions.length === 1 ? "" : "s"}`
                          : "—"}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-xs">
                        {p.createdByUsername ?? "—"}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-xs">
                        {p.createdAt ? fmt.formatDate(p.createdAt) : "—"}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setDeleteTarget(p)}
                          disabled={deleting}
                        >
                          <Trash2Icon className="size-4" />
                          <span className="sr-only">Delete</span>
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <CreatePolicyDialog open={open} onOpenChange={setOpen} />

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
          if (deleteTarget) await handleDelete(deleteTarget);
        }}
      />
    </PageShell>
  );
}
