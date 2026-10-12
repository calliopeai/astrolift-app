"use client";

import { ShellHeader } from "@/components/shell/ShellHeader";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";

import { permissionsCrumbs } from "../access/admin-crumbs";
import type { useActionSafety } from "./use-action-safety";

export type ActionSafetyViewProps = ReturnType<typeof useActionSafety>;

/**
 * Admin › Permissions › Action safety (#2203). Each sensitive action asks for
 * confirmation and a fresh sign-in by default, and an admin may approve their
 * own; the organization can turn either off here. Team, app and user rules can
 * only add to these, so a row that something narrower re-enabled says so.
 */
export function ActionSafetyView({
  rows,
  loading,
  error,
  onRetry,
  saving,
  saveError,
  onSetRule,
}: ActionSafetyViewProps) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6 p-6">
      <ShellHeader crumbs={permissionsCrumbs("safety")} title="Action safety" />
      <p className="text-muted-foreground max-w-prose text-sm">
        Sensitive actions ask for a confirmation and a fresh sign-in before they run. They are on by
        default. Turning one off here applies to the whole organization; a team, app or person can
        still be asked for more, never less. Changing these asks you to sign in again.
      </p>
      {saveError && (
        <p role="alert" className="text-destructive text-sm">
          {saveError}
        </p>
      )}
      {loading ? (
        <p role="status" className="text-muted-foreground text-sm">
          Loading…
        </p>
      ) : error ? (
        <div role="alert" className="space-y-2 text-sm">
          <p>The gates could not be read: {error}</p>
          <Button size="sm" variant="outline" onClick={onRetry}>
            Retry
          </Button>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-sm border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b">
                <th scope="col" className="text-muted-foreground p-2 text-left font-medium">
                  Action
                </th>
                <th scope="col" className="text-muted-foreground p-2 text-left font-medium">
                  Confirm and sign in again
                </th>
                <th scope="col" className="text-muted-foreground p-2 text-left font-medium">
                  Admins may approve their own
                </th>
              </tr>
            </thead>
            <tbody>
              {(rows ?? []).map((row) => {
                const busy = saving === row.action;
                const narrower = row.sources.some((s) => s !== "ORG");
                return (
                  <tr key={row.action} className="border-b last:border-0">
                    <th scope="row" className="p-2 text-left font-normal">
                      {row.label}
                      <span className="text-muted-foreground block font-mono text-xs">
                        {row.action}
                      </span>
                    </th>
                    <td className="p-2">
                      <label className="flex items-center gap-2">
                        <Checkbox
                          checked={row.enabled}
                          disabled={busy}
                          onCheckedChange={(value) =>
                            void onSetRule(row.action, {
                              enabled: value === true,
                              allowSelfApproval: row.allowSelfApproval,
                            })
                          }
                          aria-label={`Ask before: ${row.label}`}
                        />
                        <span className="text-xs">{row.enabled ? "On" : "Off"}</span>
                      </label>
                      {narrower && (
                        <span className="text-muted-foreground block text-xs">
                          Also set for a team, app or person
                        </span>
                      )}
                    </td>
                    <td className="p-2">
                      <label className="flex items-center gap-2">
                        <Checkbox
                          checked={row.allowSelfApproval}
                          disabled={busy || !row.enabled}
                          onCheckedChange={(value) =>
                            void onSetRule(row.action, {
                              enabled: row.enabled,
                              allowSelfApproval: value === true,
                            })
                          }
                          aria-label={`Admins may approve their own: ${row.label}`}
                        />
                        <span className="text-xs">
                          {row.allowSelfApproval ? "Allowed" : "Not allowed"}
                        </span>
                      </label>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
