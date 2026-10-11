"use client";

import { CheckIcon, MinusIcon } from "lucide-react";
import Link from "next/link";

import { ShellHeader } from "@/components/shell/ShellHeader";
import { Button } from "@/components/ui/button";

import { permissionsCrumbs } from "../access/admin-crumbs";
import type { useAccessPresets } from "./use-access-presets";

export type AccessPresetsViewProps = ReturnType<typeof useAccessPresets>;

const SCOPE_LABEL: Record<string, string> = {
  ORG: "Organization",
  TEAM: "Team",
  PROJECT: "Project",
  APP: "App",
};

/**
 * Admin › Permissions › Easy setup (#2272): the plain-language starting
 * points. Each preset is a stock role per scope, and the table shows what that
 * role allows and leaves out, as the server computes it from the role's
 * permissions. Assigning one is an ordinary assignment of that role.
 */
export function AccessPresetsView({ presets, loading, error, onRetry }: AccessPresetsViewProps) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6 p-6">
      <ShellHeader crumbs={permissionsCrumbs("presets")} title="Easy setup" />
      <p className="text-muted-foreground max-w-prose text-sm">
        Start from what a person needs to do. Each choice grants an existing role at the scope you
        pick, so it shows up under Assignments like any other grant. None of them makes someone an
        installation super-admin.
      </p>
      {loading ? (
        <p role="status" className="text-muted-foreground text-sm">
          Loading…
        </p>
      ) : error ? (
        <div role="alert" className="space-y-2 text-sm">
          <p>The presets could not be read: {error}</p>
          <Button size="sm" variant="outline" onClick={onRetry}>
            Retry
          </Button>
        </div>
      ) : (
        <div className="space-y-6">
          {(presets ?? []).map((preset) => (
            <section key={preset.key} aria-label={preset.label} className="space-y-2">
              <div>
                <h2 className="font-semibold">{preset.label}</h2>
                <p className="text-muted-foreground text-sm">{preset.summary}</p>
              </div>
              <div className="overflow-x-auto rounded-sm border">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b">
                      <th scope="col" className="text-muted-foreground p-2 text-left font-medium">
                        Task
                      </th>
                      {preset.roles.map((role) => (
                        <th key={role.scopeLevel} scope="col" className="p-2 text-left font-medium">
                          {SCOPE_LABEL[role.scopeLevel] ?? role.scopeLevel}
                          <span className="text-muted-foreground block font-mono text-xs font-normal">
                            {role.roleSlug}
                          </span>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(preset.roles[0]?.tasks ?? []).map((task, row) => (
                      <tr key={task.key} className="border-b last:border-0">
                        <th scope="row" className="p-2 text-left font-normal">
                          {task.label}
                        </th>
                        {preset.roles.map((role) => {
                          const granted = role.tasks[row]?.granted === true;
                          return (
                            <td key={role.scopeLevel} className="p-2">
                              {granted ? (
                                <CheckIcon className="text-success size-4" aria-label="Allowed" />
                              ) : (
                                <MinusIcon
                                  className="text-muted-foreground size-4"
                                  aria-label="Not included"
                                />
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ))}
          <p className="text-sm">
            To grant one, assign its role under{" "}
            <Link
              className="underline underline-offset-2"
              href="/administration/permissions/assignments"
            >
              Assignments
            </Link>
            .
          </p>
        </div>
      )}
    </div>
  );
}
