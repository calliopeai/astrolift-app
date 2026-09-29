import Link from "next/link";

import { principalOfBinding, SCOPE_NOUN, sourceOfBinding } from "@/components/access/access-model";
import { GrantSource } from "@/components/access/GrantSource";
import { PrincipalChip } from "@/components/access/PrincipalChip";
import type { Column } from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import type { AstroliftRoleBinding } from "@/graphql/identity/identity.types";

import { groupHref, roleHref } from "./roles-routes";
import { SCOPE_TONE } from "./scope-tone";

/**
 * The columns a role-binding list shows, shared by Assignments (every
 * binding) and a role's Holders tab (the bindings of one role, where the
 * role column is left out). A group-held binding is an IdP group mapping:
 * the principal is the group, linked to its page, and the source says so
 * (design 3.2). The sort keys are `astroliftRoleBindingsPage`'s. Pure.
 */
export function bindingColumns({
  showRole = true,
  formatDate,
}: {
  showRole?: boolean;
  formatDate: (iso: string) => string;
}): Column<AstroliftRoleBinding>[] {
  const columns: Column<AstroliftRoleBinding>[] = [
    {
      id: "principal",
      header: "Held by",
      sortKey: "name",
      cellClassName: "max-w-72",
      cell: (b) => {
        const p = principalOfBinding(b);
        return (
          <PrincipalChip
            principal={
              p.kind === "group" ? { ...p, href: groupHref(p.id), detail: "IdP group" } : p
            }
            variant="block"
          />
        );
      },
    },
  ];
  if (showRole) {
    columns.push({
      id: "role",
      header: "Role",
      sortKey: "role",
      cellClassName: "max-w-64",
      cell: (b) => (
        <Link
          href={roleHref(b.role.id)}
          className="focus-visible:ring-ring block min-w-0 rounded-sm hover:underline focus-visible:ring-2 focus-visible:outline-none"
        >
          <span className="block truncate font-medium" title={b.role.name}>
            {b.role.name}
          </span>
          <span
            className="text-muted-foreground block truncate font-mono text-xs"
            title={b.role.slug}
          >
            {b.role.slug}
          </span>
        </Link>
      ),
    });
  }
  columns.push(
    {
      id: "scope",
      header: "Where",
      sortKey: "scope",
      cellClassName: "max-w-64",
      cell: (b) => (
        <div className="flex min-w-0 flex-col items-start gap-1">
          <Badge className={`${SCOPE_TONE[b.scopeKind] ?? ""} font-mono`} variant="secondary">
            {SCOPE_NOUN[b.scopeKind] ?? b.scopeKind}
          </Badge>
          <span
            className="text-muted-foreground block max-w-full truncate text-xs"
            title={b.sourceScopeLabel || b.scopeId}
          >
            {b.sourceScopeLabel || <span className="font-mono">{b.scopeId}</span>}
          </span>
        </div>
      ),
    },
    {
      id: "source",
      header: "Source",
      cellClassName: "max-w-64",
      cell: (b) => {
        const source = sourceOfBinding(b);
        const via =
          source.via?.kind === "group"
            ? { ...source.via, href: groupHref(source.via.group) }
            : source.via;
        return <GrantSource source={{ ...source, via }} />;
      },
    },
    {
      id: "granted",
      header: "Granted",
      sortKey: "created",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (b) => formatDate(b.grantedAt),
    },
    {
      id: "expires",
      header: "Expires",
      sortKey: "expires",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (b) => (b.expiresAt ? formatDate(b.expiresAt) : "never"),
    }
  );
  return columns;
}
