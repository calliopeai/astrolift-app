"use client";

import { QueryError } from "@/components/QueryError";

import { GlobeIcon, PlusIcon, Trash2Icon } from "lucide-react";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import type { AstroliftOrganizationAllowlistedDomain } from "@/graphql/identity/identity.types";

import type { useTrustedDomains } from "./use-trusted-domains";

export type TrustedDomainsCardProps = ReturnType<typeof useTrustedDomains>;

const NONE_ROLE_VALUE = "__none__";
const NONE_REVIEW_VALUE = "auto";
const REVIEW_VALUE = "review";

/**
 * Admin › Organization › Trusted domains: the add form over the allowlist,
 * which is the section's one embedded list (search, mode and role filters,
 * numbered pages), remove in each row's `⋯`. Pure.
 */
export function TrustedDomainsCard({
  list,
  rows,
  totalCount,
  loading,
  error,
  onRetry,
  roleOptions,
  rolesLoading,
  rolesError,
  onRetryRoles,
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

  const columns: Column<AstroliftOrganizationAllowlistedDomain>[] = [
    {
      id: "domain",
      header: "Domain",
      sortKey: "domain",
      cellClassName: "max-w-80",
      cell: (row) => (
        <span className="block truncate font-mono" title={row.domain}>
          {row.domain}
        </span>
      ),
    },
    {
      id: "role",
      header: "Default role",
      sortKey: "role",
      cellClassName: "max-w-56",
      cell: (row) =>
        row.defaultRoleSlug ? (
          <Badge variant="secondary" className="max-w-full truncate font-mono">
            {row.defaultRoleSlug}
          </Badge>
        ) : (
          <span className="text-muted-foreground text-xs">none</span>
        ),
    },
    {
      id: "mode",
      header: "Mode",
      cell: (row) =>
        row.requiresReview ? (
          <Badge variant="outline">Review required</Badge>
        ) : (
          <Badge variant="outline">Auto-join</Badge>
        ),
    },
  ];

  return (
    <Section
      title="Trusted email domains"
      description={
        <>
          SSO users whose email belongs to one of these domains can join this organization without
          an explicit invitation. Set <em>Require admin review</em> to land them as pending members
          for approval before they get active access.
        </>
      }
      divided
    >
      <div className="grid min-w-0 gap-6">
        <form onSubmit={handleAdd} className="grid min-w-0 gap-3 sm:grid-cols-2 sm:items-end">
          <div className="min-w-0 space-y-2 sm:col-span-2">
            <Label htmlFor="allowlist-domain">Domain</Label>
            <Input
              id="allowlist-domain"
              placeholder="example.com"
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              autoComplete="off"
              className="font-mono"
              required
            />
          </div>
          <div className="min-w-0 space-y-2">
            <Label htmlFor="allowlist-role">Default role</Label>
            <Select
              value={roleSlug}
              onValueChange={setRoleSlug}
              disabled={rolesLoading || Boolean(rolesError)}
            >
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
            {rolesLoading && (
              <p role="status" className="text-muted-foreground text-xs">
                Loading roles…
              </p>
            )}
            <QueryError
              title="Could not load default roles"
              error={rolesError}
              onRetry={onRetryRoles}
            />
          </div>
          <div className="min-w-0 space-y-2">
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
          <div className="flex justify-end sm:col-span-2">
            <Button type="submit" disabled={adding || !domain.trim()}>
              <PlusIcon className="size-4" />
              Add domain
            </Button>
          </div>
        </form>

        <ListPage<AstroliftOrganizationAllowlistedDomain>
          embedded
          list={list}
          label="Trusted domains"
          columns={columns}
          rows={rows}
          getRowId={(row) => row.id}
          rowActions={(row) => (
            <DropdownMenuItem
              variant="destructive"
              disabled={removing}
              onSelect={() => setRemoveTarget(row)}
            >
              <Trash2Icon className="size-4" />
              Remove {row.domain}
            </DropdownMenuItem>
          )}
          loading={loading}
          error={error}
          onRetry={onRetry}
          totalCount={totalCount}
          empty={{
            icon: <GlobeIcon className="size-5" />,
            title: "No trusted domains yet",
            description:
              "SSO users without a domain match still need an explicit invitation to join.",
          }}
        />
      </div>

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
    </Section>
  );
}
