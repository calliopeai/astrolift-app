"use client";

import { ChevronRightIcon, LockIcon } from "lucide-react";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { ASTROLIFT_PERMISSIONS } from "@/lib/permissions/permissions.generated";
import { cn } from "@/lib/utils";

import { type RoleRef, SCOPE_NOUN, summarizePermissions } from "./access-model";
import { PermissionMatrix } from "./PermissionMatrix";

export interface RoleSummaryProps {
  role: RoleRef;
  /** Shows "Show permissions", which opens the read-only matrix below. */
  expandable?: boolean;
  defaultOpen?: boolean;
  /** Diff the matrix against another role, e.g. the one this was duplicated from. */
  base?: Pick<RoleRef, "name" | "permissions"> | null;
  catalog?: readonly string[];
  className?: string;
}

/**
 * A role in one line (design 3.4 step 2, 3.5): its name, slug, the level it
 * binds at, built-in or custom, how many permissions, and what it allows in
 * plain words (its own description, or a summary of its slugs). Expands to
 * the read-only `PermissionMatrix`, diffed against `base` when given. Only
 * the expanded matrix is mounted.
 */
export function RoleSummary({
  role,
  expandable = true,
  defaultOpen = false,
  base,
  catalog = ASTROLIFT_PERMISSIONS,
  className,
}: RoleSummaryProps) {
  const [open, setOpen] = React.useState(defaultOpen);
  const matrixId = React.useId();
  const summary = role.description?.trim() || summarizePermissions(role.permissions, catalog);

  return (
    <div className={cn("flex min-w-0 flex-col gap-2", className)}>
      <div className="flex min-w-0 flex-col gap-0.5">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="min-w-0 truncate font-medium" title={role.name}>
            {role.name}
          </span>
          <span
            className="text-muted-foreground min-w-0 truncate font-mono text-xs"
            title={role.slug}
          >
            {role.slug}
          </span>
          <Badge variant="outline" className="text-2xs font-mono uppercase">
            {SCOPE_NOUN[role.scopeLevel]}
          </Badge>
          {role.isSystem ? (
            <Badge variant="secondary" className="text-2xs gap-1">
              <LockIcon aria-hidden className="size-3" />
              built-in
            </Badge>
          ) : (
            <Badge variant="secondary" className="text-2xs">
              custom
            </Badge>
          )}
          <span className="text-muted-foreground font-mono text-xs tabular-nums">
            {role.permissions.length} permission{role.permissions.length === 1 ? "" : "s"}
          </span>
        </div>
        <p className="text-muted-foreground min-w-0 text-sm [overflow-wrap:anywhere]">{summary}</p>
      </div>
      {expandable && (
        <button
          type="button"
          aria-expanded={open}
          aria-controls={matrixId}
          onClick={() => setOpen((o) => !o)}
          className="text-primary focus-visible:ring-ring inline-flex w-fit items-center gap-1 rounded-sm text-xs hover:underline focus-visible:ring-2 focus-visible:outline-none"
        >
          <ChevronRightIcon
            aria-hidden
            className={cn(
              "size-3.5 transition-transform duration-[var(--motion-fast)]",
              open && "rotate-90"
            )}
          />
          {open ? "Hide permissions" : "Show permissions"}
        </button>
      )}
      {expandable && open && (
        <div id={matrixId} className="min-w-0">
          <PermissionMatrix
            permissions={role.permissions}
            catalog={catalog}
            base={base?.permissions}
            baseLabel={base?.name}
          />
        </div>
      )}
    </div>
  );
}
