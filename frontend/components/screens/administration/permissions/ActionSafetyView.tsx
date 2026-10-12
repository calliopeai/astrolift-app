"use client";

import { useTranslations } from "next-intl";

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

import type { ScopeNode } from "@/components/access/access-model";
import { useFormatters } from "@/lib/i18n/formatters";

import { permissionsCrumbs } from "../access/admin-crumbs";
import { ScopedSafetyRules } from "./ScopedSafetyRules";
import type { ActionApprovalRequestRow, useActionSafety } from "./use-action-safety";

export type ActionSafetyViewProps = ReturnType<typeof useActionSafety>;

const QUORUMS = [1, 2, 3, 4, 5];
const WHOLE_ORG = "__org__";

function appsIn(nodes: ScopeNode[]): ScopeNode[] {
  return nodes.flatMap((n) => [
    ...(n.kind === "APP" && n.slug ? [n] : []),
    ...appsIn(n.children ?? []),
  ]);
}

/**
 * Admin › Permissions › Action safety (#2203). Each sensitive action asks for
 * confirmation and a fresh sign-in by default; the organization can turn that
 * off, and add approval to a destructive delete (#2202). Team, app and user
 * rules can only add to these, so a row that something narrower changed says
 * so. Below, the approval requests still open.
 */
export function ActionSafetyView({
  rows,
  inspecting,
  onInspect,
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
  scoped,
}: ActionSafetyViewProps) {
  const t = useTranslations("actionSafety");
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6 p-6">
      <ShellHeader crumbs={permissionsCrumbs("safety")} title={t("title")} />
      <p className="text-muted-foreground max-w-prose text-sm">{t("intro")}</p>
      {saveError && (
        <p role="alert" className="text-destructive text-sm">
          {saveError}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-muted-foreground text-xs">{t("inspect")}</span>
        <Select
          value={inspecting ?? WHOLE_ORG}
          onValueChange={(value) => onInspect(value === WHOLE_ORG ? null : value)}
        >
          <SelectTrigger size="sm" className="w-auto" aria-label={t("inspect")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={WHOLE_ORG}>{t("wholeOrg")}</SelectItem>
            {[...new Map(appsIn(scoped.scopeTree.roots).map((a) => [a.slug!, a])).values()].map(
              (app) => (
                <SelectItem key={app.slug} value={app.slug!}>
                  {app.name}
                </SelectItem>
              )
            )}
          </SelectContent>
        </Select>
        {inspecting && <span className="text-muted-foreground text-xs">{t("inspectingNote")}</span>}
      </div>
      {loading ? (
        <p role="status" className="text-muted-foreground text-sm">
          {t("loading")}
        </p>
      ) : error ? (
        <div role="alert" className="space-y-2 text-sm">
          <p>{t("loadError", { error })}</p>
          <Button size="sm" variant="outline" onClick={onRetry}>
            {t("retry")}
          </Button>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-sm border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b">
                <th scope="col" className="text-muted-foreground p-2 text-left font-medium">
                  {t("columns.action")}
                </th>
                <th scope="col" className="text-muted-foreground p-2 text-left font-medium">
                  {t("columns.confirm")}
                </th>
                <th scope="col" className="text-muted-foreground p-2 text-left font-medium">
                  {t("columns.approval")}
                </th>
                <th scope="col" className="text-muted-foreground p-2 text-left font-medium">
                  {t("columns.selfApproval")}
                </th>
              </tr>
            </thead>
            <tbody>
              {(rows ?? []).map((row) => {
                const busy = saving === row.action || inspecting !== null;
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
                          aria-label={t("askBefore", { action: row.label })}
                        />
                        <span className="text-xs">{row.enabled ? t("on") : t("off")}</span>
                      </label>
                      {narrower && (
                        <span className="text-muted-foreground block text-xs">{t("narrower")}</span>
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
                              aria-label={t("requireApproval", { action: row.label })}
                            />
                            <span className="text-xs">
                              {row.approval ? t("required") : t("off")}
                            </span>
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
                                aria-label={t("approversNeeded", { action: row.label })}
                              >
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {QUORUMS.map((n) => (
                                  <SelectItem key={n} value={String(n)}>
                                    {t("approvers", { count: n })}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          )}
                        </div>
                      ) : (
                        <span className="text-muted-foreground text-xs">
                          {row.action === "secret.change" ? t("perApp") : t("notAvailable")}
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
                            aria-label={t("selfApprovalFor", { action: row.label })}
                          />
                          <span className="text-xs">
                            {row.allowSelfApproval ? t("allowed") : t("notAllowed")}
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
      {rows && <ScopedSafetyRules {...scoped} />}
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
  const t = useTranslations("actionSafety.requests");
  const fmt = useFormatters();
  return (
    <section aria-labelledby="approval-requests" className="space-y-2">
      <h2 id="approval-requests" className="text-sm font-medium">
        {t("title")}
      </h2>
      {decideError && (
        <p role="alert" className="text-destructive text-sm">
          {decideError}
        </p>
      )}
      {error ? (
        <p role="alert" className="text-sm">
          {t("loadError", { error })}
        </p>
      ) : requests.length === 0 ? (
        <p className="text-muted-foreground text-sm">{t("empty")}</p>
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
                      ? t("byYou")
                      : request.requesterEmail
                        ? t("byEmail", { email: request.requesterEmail })
                        : t("bySomeone")}
                    {" · "}
                    {t.rich("progress", {
                      approvals: request.approvals,
                      required: request.requiredApprovals,
                      mono: (chunks) => <span className="font-mono">{chunks}</span>,
                      expiry: () => (
                        <time className="font-mono" dateTime={String(request.expiresAt)}>
                          {fmt.formatDateTime(String(request.expiresAt))}
                        </time>
                      ),
                    })}
                  </span>
                </span>
                {request.status === "approved" ? (
                  <span className="text-xs">{mine ? t("approvedMine") : t("approved")}</span>
                ) : (
                  (!mine || request.allowSelfApproval) && (
                    <Button
                      size="sm"
                      disabled={busy}
                      onClick={() => void onDecide(request.id, "approve")}
                    >
                      {t("approve")}
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
                    {t("reject")}
                  </Button>
                )}
                {mine && (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy}
                    onClick={() => void onDecide(request.id, "cancel")}
                  >
                    {t("withdraw")}
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
