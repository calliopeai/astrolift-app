"use client";

import {
  ClockIcon,
  GitBranchIcon,
  HandIcon,
  Loader2Icon,
  PencilIcon,
  ServerCogIcon,
  ZapIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Can } from "@/components/Can";
import { Card, CardContent } from "@/components/ui/card";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import type { AstroliftRegisteredApp, TriggerMode } from "@/graphql/registry/registry.types";

import type { DeployStrategyValues } from "./use-deploy-strategy";

// Per-mode icon mapping. Labels + hints come from the i18n bundle so
// every locale renders consistently; the icon is purely presentational.
const MODE_ICONS: Record<TriggerMode, React.ComponentType<{ className?: string }>> = {
  auto_on_push: ZapIcon,
  manual: HandIcon,
  external_ci: ServerCogIcon,
  cron: ClockIcon,
};

/** The app fields the card and its edit sheet read. */
export type DeployStrategyApp = Pick<
  AstroliftRegisteredApp,
  "triggerMode" | "deployBranch" | "defaultBranch" | "previewEnabled" | "cronExpression"
>;

export interface DeployStrategyCardViewProps {
  app: DeployStrategyApp;
  saving: boolean;
  /** Resolves true when the save landed; the sheet closes then. */
  onSave: (values: DeployStrategyValues) => Promise<boolean>;
  /** Stories only: start with the edit sheet open. */
  defaultOpen?: boolean;
}

/**
 * Read view of the deploy strategy + edit affordance via a side sheet.
 * The strategy here is the trio of trigger mode, deploy branch, and
 * preview state — the three knobs that decide *when* and *from where* a
 * deploy lands. Approval policy is environment-scoped and lives on the
 * Controls section below.
 */
export function DeployStrategyCardView({
  app,
  saving,
  onSave,
  defaultOpen = false,
}: DeployStrategyCardViewProps) {
  const t = useTranslations("apps.deployStrategy");
  const [open, setOpen] = useState(defaultOpen);
  const mode: TriggerMode = (app.triggerMode as TriggerMode) || "manual";
  const Icon = MODE_ICONS[mode] ?? MODE_ICONS.manual;

  return (
    <Card>
      <CardContent className="flex items-start justify-between gap-3">
        <div>
          <p className="text-muted-foreground text-2xs font-medium tracking-wide uppercase">
            {t("eyebrow")}
          </p>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="gap-1.5 py-1">
              <Icon className="size-3.5 text-[var(--brand-primary)]" />
              <span className="font-medium">{t(`modes.${mode}.label`)}</span>
            </Badge>
            <Badge variant="secondary" className="text-2xs gap-1 font-mono">
              <GitBranchIcon className="size-3" />
              {app.deployBranch || app.defaultBranch}
            </Badge>
            {app.previewEnabled && (
              <Badge variant="outline" className="text-2xs">
                {t("previewEnvironments")}
              </Badge>
            )}
          </div>
          <p className="text-muted-foreground mt-2 max-w-xl text-xs">{t(`modes.${mode}.hint`)}</p>
        </div>
        <Can permission="app.update">
          <Button variant="ghost" size="sm" onClick={() => setOpen(true)}>
            <PencilIcon className="size-3.5" />
            {t("edit")}
          </Button>
        </Can>
      </CardContent>

      {open && (
        <EditStrategySheet
          app={app}
          open={open}
          onOpenChange={setOpen}
          saving={saving}
          onSave={onSave}
        />
      )}
    </Card>
  );
}

function EditStrategySheet({
  app,
  open,
  onOpenChange,
  saving: loading,
  onSave,
}: {
  app: DeployStrategyApp;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  saving: boolean;
  onSave: (values: DeployStrategyValues) => Promise<boolean>;
}) {
  const t = useTranslations("apps.deployStrategy");
  const [triggerMode, setTriggerMode] = useState<TriggerMode>(app.triggerMode);
  const [deployBranch, setDeployBranch] = useState(app.deployBranch || app.defaultBranch);
  const [previewEnabled, setPreviewEnabled] = useState(app.previewEnabled);
  const [cronExpression, setCronExpression] = useState(app.cronExpression || "");

  async function handleSave() {
    if (await onSave({ triggerMode, deployBranch, previewEnabled, cronExpression })) {
      onOpenChange(false);
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex w-full flex-col gap-4 sm:max-w-md">
        <SheetHeader>
          <SheetTitle>{t("sheetTitle")}</SheetTitle>
          <SheetDescription>{t("sheetDescription")}</SheetDescription>
        </SheetHeader>

        <div className="flex flex-1 flex-col gap-4 px-4">
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="text-muted-foreground text-xs">{t("triggerModeLabel")}</span>
            <select
              value={triggerMode}
              onChange={(e) => setTriggerMode(e.target.value as TriggerMode)}
              className="border-input bg-background rounded-md border px-2 py-2 text-sm"
            >
              <option value="auto_on_push">{t("modes.auto_on_push.label")}</option>
              <option value="cron">{t("modes.cron.label")}</option>
              <option value="manual">{t("modes.manual.label")}</option>
              <option value="external_ci">{t("modes.external_ci.label")}</option>
            </select>
          </label>

          {triggerMode === "cron" && (
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="text-muted-foreground text-xs">{t("cronExpressionLabel")}</span>
              <input
                type="text"
                value={cronExpression}
                onChange={(e) => setCronExpression(e.target.value)}
                placeholder="0 6 * * *"
                className="border-input bg-background rounded-md border px-2 py-2 font-mono text-sm"
              />
              <span className="text-muted-foreground text-2xs">{t("cronExpressionHelp")}</span>
            </label>
          )}

          <label className="flex flex-col gap-1.5 text-sm">
            <span className="text-muted-foreground text-xs">{t("deployBranchLabel")}</span>
            <input
              type="text"
              value={deployBranch}
              onChange={(e) => setDeployBranch(e.target.value)}
              placeholder={app.defaultBranch}
              className="border-input bg-background rounded-md border px-2 py-2 font-mono text-sm"
            />
            <span className="text-muted-foreground text-2xs">
              {t.rich("deployBranchHelp", {
                branch: () => <span className="font-mono">{app.defaultBranch || "main"}</span>,
              })}
            </span>
          </label>

          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              checked={previewEnabled}
              onChange={(e) => setPreviewEnabled(e.target.checked)}
              className="mt-0.5 size-4"
            />
            <span>
              <span className="font-medium">{t("previewEnvironments")}</span>
              <span className="text-muted-foreground block text-xs">
                {t("previewEnvironmentsHelp")}
              </span>
            </span>
          </label>
        </div>

        <SheetFooter className="flex flex-row justify-end gap-2 border-t px-4 pt-3">
          <SheetClose asChild>
            <Button variant="outline" size="sm" disabled={loading}>
              {t("cancel")}
            </Button>
          </SheetClose>
          <Button size="sm" onClick={handleSave} disabled={loading}>
            {loading && <Loader2Icon className="size-3.5 animate-spin" />}
            {t("save")}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
