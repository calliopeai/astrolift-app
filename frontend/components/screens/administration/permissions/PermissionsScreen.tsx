"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { ShellHeader } from "@/components/shell/ShellHeader";

import { type PermissionsPage, permissionsCrumbs } from "../access/admin-crumbs";

export interface PermissionsScreenProps {
  /** `admin.permissions_enabled`; while false the screen says so and mounts nothing. */
  enabled: boolean;
  /** Which of Roles, Assignments and Diagnostics this route is. */
  page: PermissionsPage;
  /**
   * The page itself, which owns its header. Rendered only while enabled, so a
   * container passed here runs its queries only then.
   */
  children: React.ReactNode;
}

/**
 * Admin › Permissions: Roles, Assignments and Diagnostics, each its own route
 * and header (spec 44 §4.4: one row of tabs, and on a list those are its
 * views), moved between with the `Permissions ▾` crumb. This is the gate all
 * three share.
 */
export function PermissionsScreen({ enabled, page, children }: PermissionsScreenProps) {
  const t = useTranslations("adminPermissions");
  // Screen gate: Permissions ships behind `admin.permissions_enabled`
  // (#1206). The nav entry is filtered out while the flag is off, but the
  // route stays reachable, so this used to `return null` and a direct hit
  // rendered a blank page — indistinguishable from the screen being broken.
  // It says so instead, and points at the flipper that turns it on.
  //
  // The flag reads false until the server-info handshake resolves, and the
  // page never mounts from here, so no roles or bindings queries fire while
  // gated.
  if (enabled) return <>{children}</>;

  const title = t(`pages.${page}`);
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6 p-6">
      <ShellHeader crumbs={permissionsCrumbs(page)} title={title} />
      <div className="text-muted-foreground max-w-prose rounded-md border border-dashed p-6 text-sm">
        <p className="text-foreground font-medium">{t("off")}</p>
        <p className="mt-2">
          {t.rich("offHint", {
            flag: "admin.permissions_enabled",
            code: (chunks) => <code className="font-mono">{chunks}</code>,
            link: (chunks) => (
              <Link className="underline underline-offset-2" href="/administration/features">
                {chunks}
              </Link>
            ),
          })}
        </p>
      </div>
    </div>
  );
}
