"use client";

import { useTranslations } from "next-intl";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/** One app and its deploy-approval setting, as guided approval reads it. */
export interface DeployApprovalApp {
  id: string;
  slug: string;
  name: string;
  requiresApproval: boolean;
  minimumApprovals: number;
  approverTeamId: string | null;
  approverUserIds: string[];
  version: number;
  /** Whether the viewer holds `app.update` on this app, which changing it needs. */
  canUpdate: boolean;
}

export interface DeployApprovalChange {
  requiresApproval: boolean;
  minimumApprovals: number;
  approverTeamId: string | null;
}

export interface DeployApprovalSetupProps {
  apps: DeployApprovalApp[] | null;
  teams: { id: string; name: string }[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  /** Writes the setting; resolves to an error message, or null when saved. */
  onSave: (app: DeployApprovalApp, change: DeployApprovalChange) => Promise<string | null>;
  /** Stories: the app already picked. */
  initialAppId?: string;
}

const QUORUMS = [1, 2, 3, 4, 5];

/**
 * Easy setup › Require approval before deploys (#2272). Guided approval
 * writes the app's own deploy-approval setting (requires approval, how many
 * approvals, which team approves), not a policy: a deploy then waits for
 * approvals instead of being refused. The setting is the whole app's, so the
 * form says so before anything is saved, and it is read-only for anyone
 * without `app.update` on the app.
 */
export function DeployApprovalSetup({
  apps,
  teams,
  loading,
  error,
  onRetry,
  onSave,
  initialAppId,
}: DeployApprovalSetupProps) {
  const t = useTranslations("adminPresets.approval");
  const [appId, setAppId] = React.useState(initialAppId ?? "");
  const app = apps?.find((a) => a.id === appId) ?? null;
  return (
    <section aria-labelledby="deploy-approval" className="space-y-3">
      <div>
        <h2 id="deploy-approval" className="font-semibold">
          {t("title")}
        </h2>
        <p className="text-muted-foreground max-w-prose text-sm">{t("intro")}</p>
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
      ) : (apps ?? []).length === 0 ? (
        <p className="text-muted-foreground text-sm">{t("noApps")}</p>
      ) : (
        <div className="space-y-3 rounded-sm border p-3 text-sm">
          <Select value={appId} onValueChange={setAppId}>
            <SelectTrigger size="sm" className="w-auto min-w-48" aria-label={t("app")}>
              <SelectValue placeholder={t("pickApp")} />
            </SelectTrigger>
            <SelectContent>
              {(apps ?? []).map((a) => (
                <SelectItem key={a.id} value={a.id}>
                  {a.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {app && <AppApproval key={app.id} app={app} teams={teams} onSave={onSave} />}
        </div>
      )}
    </section>
  );
}

function AppApproval({
  app,
  teams,
  onSave,
}: {
  app: DeployApprovalApp;
  teams: { id: string; name: string }[];
  onSave: DeployApprovalSetupProps["onSave"];
}) {
  const t = useTranslations("adminPresets.approval");
  const [on, setOn] = React.useState(app.requiresApproval);
  const [quorum, setQuorum] = React.useState(Math.max(app.minimumApprovals || 1, 1));
  const [team, setTeam] = React.useState(app.approverTeamId ?? "");
  const [saving, setSaving] = React.useState(false);
  const [result, setResult] = React.useState<{ ok: boolean; message: string } | null>(null);

  const changed = on
    ? !app.requiresApproval ||
      quorum !== app.minimumApprovals ||
      team !== (app.approverTeamId ?? "") ||
      app.approverUserIds.length > 0
    : app.requiresApproval;
  const missingTeam = on && !team;

  async function save() {
    if (!changed || missingTeam || saving) return;
    setSaving(true);
    setResult(null);
    const failed = await onSave(app, {
      requiresApproval: on,
      minimumApprovals: quorum,
      approverTeamId: on ? team : null,
    });
    setSaving(false);
    setResult(failed ? { ok: false, message: failed } : { ok: true, message: t("saved") });
  }

  return (
    <div className="space-y-3">
      <p className="border-warning-border bg-warning-bg text-warning-fg max-w-prose rounded-sm border p-2 text-xs">
        {t("wholeApp", { app: app.name })}
      </p>
      {!app.canUpdate && (
        <p role="note" className="text-muted-foreground max-w-prose text-xs">
          {t("cannotChange", { app: app.name })}
        </p>
      )}
      <fieldset className="space-y-3" disabled={!app.canUpdate || saving}>
        <legend className="sr-only">{t("legend", { app: app.name })}</legend>
        <label className="flex items-center gap-2">
          <Checkbox
            checked={on}
            disabled={!app.canUpdate || saving}
            onCheckedChange={(v) => setOn(v === true)}
          />
          <span>{t("require")}</span>
        </label>
        {on && (
          <div className="flex flex-wrap items-center gap-3">
            <Select value={team} onValueChange={setTeam} disabled={!app.canUpdate || saving}>
              <SelectTrigger size="sm" className="w-auto min-w-40" aria-label={t("team")}>
                <SelectValue placeholder={t("pickTeam")} />
              </SelectTrigger>
              <SelectContent>
                {teams.map((x) => (
                  <SelectItem key={x.id} value={x.id}>
                    {x.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={String(quorum)}
              onValueChange={(v) => setQuorum(Number(v))}
              disabled={!app.canUpdate || saving}
            >
              <SelectTrigger size="sm" className="w-auto" aria-label={t("quorum")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {QUORUMS.map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {t("approvals", { count: n })}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
        {on && app.approverUserIds.length > 0 && (
          <p className="text-muted-foreground max-w-prose text-xs">{t("replacesPeople")}</p>
        )}
        {on && <p className="text-muted-foreground max-w-prose text-xs">{t("approversNeed")}</p>}
        <Button size="sm" disabled={!changed || missingTeam} onClick={() => void save()}>
          {saving ? t("saving") : t("save")}
        </Button>
      </fieldset>
      {result && (
        <p
          role={result.ok ? "status" : "alert"}
          className={result.ok ? "text-success-fg text-xs" : "text-destructive text-xs"}
        >
          {result.message}
        </p>
      )}
    </div>
  );
}
