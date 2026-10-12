"use client";

import { Trash2Icon, UsersIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { principalOfBinding } from "@/components/access/access-model";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ListPage } from "@/components/list/ListPage";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import type { AstroliftRoleBinding } from "@/graphql/identity/identity.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { bindingColumns } from "./binding-columns";
import type { useRoleHolders } from "./use-role-holders";

export type RoleHoldersTabProps = ReturnType<typeof useRoleHolders> & {
  roleName: string;
  /** Where Grant access goes, preselected to this role; absent without manage. */
  grantHref?: string;
};

/**
 * A role's Holders tab (design 3.5): who holds this role and where, as an
 * embedded list under the role's tabs. A binding held by an IdP group is the
 * group's mapping to this role; it links to the group, where mappings are
 * managed (design 3.2). Revoke removes the binding itself. Pure.
 */
export function RoleHoldersTab({
  list,
  page,
  canManage,
  revoking,
  onRevoke,
  roleName,
  grantHref,
}: RoleHoldersTabProps) {
  const fmt = useFormatters();
  const t = useTranslations("adminBindings");
  const scopes = useTranslations("shared.access.scope");
  const [target, setTarget] = React.useState<AstroliftRoleBinding | null>(null);
  const columns = bindingColumns({
    showRole: false,
    formatDate: fmt.formatDate,
    t,
    scopeNoun: (kind) => (scopes.has(kind) ? scopes(kind) : kind),
  });

  return (
    <>
      <ListPage<AstroliftRoleBinding>
        embedded
        list={list}
        label={t("holders")}
        columns={columns}
        rows={page.rows}
        getRowId={(b) => b.id}
        loading={page.loading}
        stale={page.stale}
        error={page.error}
        onRetry={page.refetch}
        totalCount={page.totalCount}
        nextCursor={page.nextCursor}
        rowActions={
          canManage
            ? (b) => (
                <DropdownMenuItem
                  variant="destructive"
                  disabled={revoking}
                  onSelect={() => setTarget(b)}
                >
                  <Trash2Icon className="size-4" />
                  {t("revoke")}
                </DropdownMenuItem>
              )
            : undefined
        }
        empty={{
          icon: <UsersIcon className="size-5" />,
          title: t("holdersEmptyTitle"),
          description: t("holdersEmptyDescription", { role: roleName }),
          actionHref: canManage ? grantHref : undefined,
          actionLabel: canManage && grantHref ? t("grantAccess") : undefined,
        }}
      />

      <ConfirmDialog
        open={target !== null}
        onOpenChange={(next) => {
          if (!next) setTarget(null);
        }}
        title={
          target
            ? t("revokeFrom", { role: roleName, name: principalOfBinding(target).name })
            : t("revokeFallback")
        }
        description={target && !target.user ? t("revokeGroup") : t("revokeHolder")}
        confirmLabel={t("revoke")}
        destructive
        onConfirm={async () => {
          if (target) await onRevoke(target);
        }}
      />
    </>
  );
}
