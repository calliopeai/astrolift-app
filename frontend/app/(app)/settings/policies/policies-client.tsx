"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { BookOpenIcon, PlusIcon, ScaleIcon, Trash2Icon } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

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
import {
  CREATE_POLICY,
  SOFT_DELETE_POLICY,
} from "@/graphql/identity/identity.mutations";
import { LIST_POLICIES } from "@/graphql/identity/identity.queries";
import type {
  AstroliftPolicy,
  MutationResult,
} from "@/graphql/identity/identity.types";

import { CreatePolicyDialog } from "./create-policy-dialog";

interface Resp {
  astroliftPolicies: AstroliftPolicy[];
}

const effectStyles: Record<string, string> = {
  ALLOW: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  DENY: "bg-red-500/15 text-red-700 dark:text-red-300",
};

export function PoliciesClient() {
  const [open, setOpen] = React.useState(false);
  const policies = useQuery<Resp>(LIST_POLICIES);

  const [softDelete, { loading: deleting }] = useMutation<{
    softDeletePolicy: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_POLICY, {
    refetchQueries: [{ query: LIST_POLICIES }],
    awaitRefetchQueries: true,
  });

  async function handleDelete(p: AstroliftPolicy) {
    if (!confirm(`Delete policy ${p.slug}? Slug becomes reclaimable.`)) return;
    const { data } = await softDelete({ variables: { input: { id: p.id } } });
    if (data?.softDeletePolicy.ok) {
      toast.success(`Deleted ${p.slug}`);
    } else {
      toast.error(data?.softDeletePolicy.errors?.[0]?.message ?? "Delete failed");
    }
  }

  const list = policies.data?.astroliftPolicies ?? [];

  return (
    <PageShell
      title="ABAC policies"
      description="Runtime predicates evaluated after RBAC. Policies can only deny — they never grant beyond role bindings."
      actions={
        <>
          <Button asChild size="sm" variant="outline">
            <Link href="/documentation/policies">
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
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((p) => (
                  <TableRow key={p.id}>
                    <TableCell>
                      <div className="font-medium">{p.name}</div>
                      <div className="text-muted-foreground font-mono text-xs">
                        {p.slug}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{p.scopeLevel}</Badge>
                    </TableCell>
                    <TableCell>
                      <Badge className={effectStyles[p.effect]} variant="secondary">
                        {p.effect}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {p.actionPattern}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {Array.isArray(p.conditions) && p.conditions.length > 0
                        ? `${p.conditions.length} condition${p.conditions.length === 1 ? "" : "s"}`
                        : "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => handleDelete(p)}
                        disabled={deleting}
                      >
                        <Trash2Icon className="size-4" />
                        <span className="sr-only">Delete</span>
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <CreatePolicyDialog open={open} onOpenChange={setOpen} />
    </PageShell>
  );
}
