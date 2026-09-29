"use client";

import { Trash2Icon, UsersIcon } from "lucide-react";
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
  const [target, setTarget] = React.useState<AstroliftRoleBinding | null>(null);
  const columns = bindingColumns({ showRole: false, formatDate: fmt.formatDate });

  return (
    <>
      <ListPage<AstroliftRoleBinding>
        embedded
        list={list}
        label="Holders"
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
                  Revoke
                </DropdownMenuItem>
              )
            : undefined
        }
        empty={{
          icon: <UsersIcon className="size-5" />,
          title: "Nobody holds this role",
          description: `Grant ${roleName} to a person or a group and they appear here, with where it applies.`,
          actionHref: canManage ? grantHref : undefined,
          actionLabel: canManage && grantHref ? "Grant access" : undefined,
        }}
      />

      <ConfirmDialog
        open={target !== null}
        onOpenChange={(next) => {
          if (!next) setTarget(null);
        }}
        title={target ? `Revoke ${roleName} from ${principalOfBinding(target).name}?` : "Revoke?"}
        description={
          target && !target.user
            ? "Removes this group's mapping to the role. The group's members keep any access other bindings give them."
            : "They lose what this role granted here. Other bindings they hold stay in effect."
        }
        confirmLabel="Revoke"
        destructive
        onConfirm={async () => {
          if (target) await onRevoke(target);
        }}
      />
    </>
  );
}
