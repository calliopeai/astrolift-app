"use client";

import { ShellHeader } from "@/components/shell/ShellHeader";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { permissionsCrumbs } from "../access/admin-crumbs";
import type { ActionApprovalRequestRow, useActionSafety } from "./use-action-safety";

export type ActionSafetyViewProps = ReturnType<typeof useActionSafety>;

const QUORUMS = [1, 2, 3, 4, 5];

/**
 * Admin › Permissions › Action safety (#2203). Each sensitive action asks for
 * confirmation and a fresh sign-in by default; the organization can turn that
 * off, and add approval to a destructive delete (#2202). Team, app and user
 * rules can only add to these, so a row that something narrower changed says
 * so. Below, the approval requests still open.
 */
export function ActionSafetyView({
  rows,
  loading,
  error,
  onRetry,
  saving,
  saveError,
  onSetRule,
  requests,
  requestsError,
  deciding,
  decideError,
  onDecide,
}: ActionSafetyViewProps) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6 p-6">
      <ShellHeader crumbs={permissionsCrumbs("safety")} title="Action safety" />
      <p className="text-muted-foreground max-w-prose text-sm">
        Sensitive actions ask for a confirmation and a fresh sign-in before they run. They are on by
        default. A destructive delete can also wait for approval from other admins. Changes here
        apply to the whole organization; a team, app or person can still be asked for more, never
        less. Changing these asks you to sign in again.
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
                  Approval
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
                            void onSetRule(row.action, { enabled: value === true })
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
                      {row.approvable ? (
                        <div className="flex items-center gap-3">
                          <label className="flex items-center gap-2">
                            <Checkbox
                              checked={row.approval}
                              disabled={busy}
                              onCheckedChange={(value) =>
                                void onSetRule(row.action, { requireApproval: value === true })
                              }
                              aria-label={`Require approval: ${row.label}`}
                            />
                            <span className="text-xs">{row.approval ? "Required" : "Off"}</span>
                          </label>
                          {row.approval && (
                            <Select
                              value={String(row.minimumApprovals)}
                              disabled={busy}
                              onValueChange={(value) =>
                                void onSetRule(row.action, { minimumApprovals: Number(value) })
                              }
                            >
                              <SelectTrigger
                                size="sm"
                                className="w-auto"
                                aria-label={`Approvers needed: ${row.label}`}
                              >
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {QUORUMS.map((n) => (
                                  <SelectItem key={n} value={String(n)}>
                                    {n} {n === 1 ? "approver" : "approvers"}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          )}
                        </div>
                      ) : (
                        <span className="text-muted-foreground text-xs">
                          {row.action === "secret.change"
                            ? "Set per app, under secret approval"
                            : "Not available"}
                        </span>
                      )}
                    </td>
                    <td className="p-2">
                      {row.approvable ? (
                        <label className="flex items-center gap-2">
                          <Checkbox
                            checked={row.allowSelfApproval}
                            disabled={busy || !row.approval}
                            onCheckedChange={(value) =>
                              void onSetRule(row.action, { allowSelfApproval: value === true })
                            }
                            aria-label={`Admins may approve their own: ${row.label}`}
                          />
                          <span className="text-xs">
                            {row.allowSelfApproval ? "Allowed" : "Not allowed"}
                          </span>
                        </label>
                      ) : (
                        <span className="text-muted-foreground text-xs">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <ApprovalRequests
        requests={requests}
        error={requestsError}
        deciding={deciding}
        decideError={decideError}
        onDecide={onDecide}
      />
    </div>
  );
}

function ApprovalRequests({
  requests,
  error,
  deciding,
  decideError,
  onDecide,
}: {
  requests: ActionApprovalRequestRow[];
  error: string | null;
  deciding: string | null;
  decideError: string | null;
  onDecide: ActionSafetyViewProps["onDecide"];
}) {
  return (
    <section aria-labelledby="approval-requests" className="space-y-2">
      <h2 id="approval-requests" className="text-sm font-medium">
        Approval requests
      </h2>
      {decideError && (
        <p role="alert" className="text-destructive text-sm">
          {decideError}
        </p>
      )}
      {error ? (
        <p role="alert" className="text-sm">
          The requests could not be read: {error}
        </p>
      ) : requests.length === 0 ? (
        <p className="text-muted-foreground text-sm">No open requests.</p>
      ) : (
        <ul className="divide-y rounded-sm border text-sm">
          {requests.map((request) => {
            const busy = deciding === request.id;
            const mine = request.viewerIsRequester;
            return (
              <li
                key={request.id}
                aria-label={`${request.label}: ${request.targetLabel}`}
                className="flex flex-wrap items-center gap-x-4 gap-y-1 p-2"
              >
                <span className="min-w-0 flex-1">
                  {request.label} <span className="font-mono text-xs">{request.targetLabel}</span>
                  <span className="text-muted-foreground block text-xs">
                    {mine
                      ? "Requested by you"
                      : `Requested by ${request.requesterEmail ?? "someone"}`}
                    {" · "}
                    <span className="font-mono">
                      {request.approvals}/{request.requiredApprovals}
                    </span>{" "}
                    approved · expires{" "}
                    <time className="font-mono" dateTime={String(request.expiresAt)}>
                      {new Date(String(request.expiresAt)).toLocaleString()}
                    </time>
                  </span>
                </span>
                {request.status === "approved" ? (
                  <span className="text-xs">
                    {mine ? "Approved: run it again to go ahead" : "Approved"}
                  </span>
                ) : (
                  (!mine || request.allowSelfApproval) && (
                    <Button
                      size="sm"
                      disabled={busy}
                      onClick={() => void onDecide(request.id, "approve")}
                    >
                      Approve
                    </Button>
                  )
                )}
                {request.status === "pending" && !mine && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() => void onDecide(request.id, "reject")}
                  >
                    Reject
                  </Button>
                )}
                {mine && (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => void onDecide(request.id, "cancel")}
                  >
                    Withdraw
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
