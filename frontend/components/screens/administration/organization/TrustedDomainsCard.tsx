"use client";

import { QueryError } from "@/components/QueryError";

import { GlobeIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { useTranslations } from "next-intl";
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
  const t = useTranslations("adminTrustedDomains");
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
      header: t("domain"),
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
      header: t("defaultRole"),
      sortKey: "role",
      cellClassName: "max-w-56",
      cell: (row) =>
        row.defaultRoleSlug ? (
          <Badge variant="secondary" className="max-w-full truncate font-mono">
            {row.defaultRoleSlug}
          </Badge>
        ) : (
          <span className="text-muted-foreground text-xs">{t("none")}</span>
        ),
    },
    {
      id: "mode",
      header: t("mode"),
      cell: (row) =>
        row.requiresReview ? (
          <Badge variant="outline">{t("reviewRequired")}</Badge>
        ) : (
          <Badge variant="outline">{t("autoJoin")}</Badge>
        ),
    },
  ];

  return (
    <Section
      title={t("title")}
      description={t.rich("description", { em: (chunks) => <em>{chunks}</em> })}
      divided
    >
      <div className="grid min-w-0 gap-6">
        <form onSubmit={handleAdd} className="grid min-w-0 gap-3 sm:grid-cols-2 sm:items-end">
          <div className="min-w-0 space-y-2 sm:col-span-2">
            <Label htmlFor="allowlist-domain">{t("domain")}</Label>
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
            <Label htmlFor="allowlist-role">{t("defaultRole")}</Label>
            <Select
              value={roleSlug}
              onValueChange={setRoleSlug}
              disabled={rolesLoading || Boolean(rolesError)}
            >
              <SelectTrigger id="allowlist-role">
                <SelectValue placeholder={t("noneOption")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE_ROLE_VALUE}>{t("noneOption")}</SelectItem>
                {roleOptions.map((r) => (
                  <SelectItem key={r.id} value={r.slug}>
                    {r.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {rolesLoading && (
              <p role="status" className="text-muted-foreground text-xs">
                {t("loadingRoles")}
              </p>
            )}
            <QueryError title={t("rolesError")} error={rolesError} onRetry={onRetryRoles} />
          </div>
          <div className="min-w-0 space-y-2">
            <Label htmlFor="allowlist-review">{t("onSignIn")}</Label>
            <Select value={reviewMode} onValueChange={setReviewMode}>
              <SelectTrigger id="allowlist-review">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE_REVIEW_VALUE}>{t("autoJoinNow")}</SelectItem>
                <SelectItem value={REVIEW_VALUE}>{t("requireReview")}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex justify-end sm:col-span-2">
            <Button type="submit" disabled={adding || !domain.trim()}>
              <PlusIcon className="size-4" />
              {t("addDomain")}
            </Button>
          </div>
        </form>

        <ListPage<AstroliftOrganizationAllowlistedDomain>
          embedded
          list={list}
          label={t("listLabel")}
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
              {t("removeDomain", { domain: row.domain })}
            </DropdownMenuItem>
          )}
          loading={loading}
          error={error}
          onRetry={onRetry}
          totalCount={totalCount}
          empty={{
            icon: <GlobeIcon className="size-5" />,
            title: t("emptyTitle"),
            description: t("emptyDescription"),
          }}
        />
      </div>

      <ConfirmDialog
        open={removeTarget !== null}
        onOpenChange={(next) => {
          if (!next) setRemoveTarget(null);
        }}
        title={
          removeTarget
            ? t("confirmTitle", { domain: removeTarget.domain })
            : t("confirmTitleFallback")
        }
        description={t("confirmDescription")}
        confirmLabel={t("confirmLabel")}
        destructive
        onConfirm={async () => {
          if (removeTarget) await onRemove(removeTarget);
        }}
      />
    </Section>
  );
}
