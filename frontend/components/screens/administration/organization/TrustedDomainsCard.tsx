"use client";

import { PlusIcon, Trash2Icon } from "lucide-react";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { AstroliftOrganizationAllowlistedDomain } from "@/graphql/identity/identity.types";

import type { useTrustedDomains } from "./use-trusted-domains";

export type TrustedDomainsCardProps = ReturnType<typeof useTrustedDomains>;

const NONE_ROLE_VALUE = "__none__";
const NONE_REVIEW_VALUE = "auto";
const REVIEW_VALUE = "review";

export function TrustedDomainsCard({
  rows,
  loading,
  roleOptions,
  adding,
  removing,
  onAdd,
  onRemove,
}: TrustedDomainsCardProps) {
  const [domain, setDomain] = React.useState("");
  const [roleSlug, setRoleSlug] = React.useState<string>(NONE_ROLE_VALUE);
  const [reviewMode, setReviewMode] = React.useState<string>(NONE_REVIEW_VALUE);

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    const added = await onAdd({
      domain,
      defaultRoleSlug: roleSlug && roleSlug !== NONE_ROLE_VALUE ? roleSlug : null,
      requiresReview: reviewMode === REVIEW_VALUE,
    });
    if (added) {
      setDomain("");
      setRoleSlug(NONE_ROLE_VALUE);
      setReviewMode(NONE_REVIEW_VALUE);
    }
  }

  const [removeTarget, setRemoveTarget] =
    React.useState<AstroliftOrganizationAllowlistedDomain | null>(null);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Trusted email domains</CardTitle>
        <CardDescription>
          SSO users whose email belongs to one of these domains can join this organization without
          an explicit invitation. Set <em>Require admin review</em> to land them as pending members
          for approval before they get active access.
        </CardDescription>
      </CardHeader>

      <CardContent className="grid gap-6">
        <form
          onSubmit={handleAdd}
          className="grid gap-3 sm:grid-cols-[1fr_180px_180px_auto] sm:items-end"
        >
          <div className="space-y-2">
            <Label htmlFor="allowlist-domain">Domain</Label>
            <Input
              id="allowlist-domain"
              placeholder="example.com"
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              autoComplete="off"
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="allowlist-role">Default role</Label>
            <Select value={roleSlug} onValueChange={setRoleSlug}>
              <SelectTrigger id="allowlist-role">
                <SelectValue placeholder="None" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE_ROLE_VALUE}>None</SelectItem>
                {roleOptions.map((r) => (
                  <SelectItem key={r.id} value={r.slug}>
                    {r.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="allowlist-review">On sign-in</Label>
            <Select value={reviewMode} onValueChange={setReviewMode}>
              <SelectTrigger id="allowlist-review">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE_REVIEW_VALUE}>Auto-join immediately</SelectItem>
                <SelectItem value={REVIEW_VALUE}>Require admin review</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <Button type="submit" disabled={adding || !domain.trim()}>
            <PlusIcon className="size-4" />
            Add domain
          </Button>
        </form>

        {loading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : rows.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            No trusted domains yet. SSO users without a domain match still need an explicit
            invitation to join.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Domain</TableHead>
                <TableHead>Default role</TableHead>
                <TableHead>Mode</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.id}>
                  <TableCell className="font-mono">{row.domain}</TableCell>
                  <TableCell>
                    {row.defaultRoleSlug ? (
                      <Badge variant="secondary">{row.defaultRoleSlug}</Badge>
                    ) : (
                      <span className="text-muted-foreground text-xs">none</span>
                    )}
                  </TableCell>
                  <TableCell>
                    {row.requiresReview ? (
                      <Badge variant="outline">Review required</Badge>
                    ) : (
                      <Badge variant="outline">Auto-join</Badge>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={removing}
                      onClick={() => setRemoveTarget(row)}
                      aria-label={`Remove ${row.domain}`}
                    >
                      <Trash2Icon className="size-4" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>

      <ConfirmDialog
        open={removeTarget !== null}
        onOpenChange={(next) => {
          if (!next) setRemoveTarget(null);
        }}
        title={removeTarget ? `Remove ${removeTarget.domain}?` : "Remove domain?"}
        description="New sign-ins from this domain stop auto-joining the org and need an explicit invitation. Existing members keep their access and role bindings — this is a forward-only change."
        confirmLabel="Remove domain"
        destructive
        onConfirm={async () => {
          if (removeTarget) await onRemove(removeTarget);
        }}
      />
    </Card>
  );
}
