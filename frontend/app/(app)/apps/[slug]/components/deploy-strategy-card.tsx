"use client";

import { useMutation } from "@apollo/client/react";
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
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Can } from "@/components/Can";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { UPDATE_APP } from "@/graphql/registry/registry.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp, TriggerMode } from "@/graphql/registry/registry.types";
import type { MutationResult } from "@/graphql/identity/identity.types";

// Per-mode icon mapping. Labels + hints come from the i18n bundle so
// every locale renders consistently; the icon is purely presentational.
const MODE_ICONS: Record<TriggerMode, React.ComponentType<{ className?: string }>> = {
  auto_on_push: ZapIcon,
  manual: HandIcon,
  external_ci: ServerCogIcon,
  cron: ClockIcon,
};

interface UpdateResp {
  updateApp: MutationResult<Partial<AstroliftRegisteredApp>>;
}

interface Props {
  app: AstroliftRegisteredApp;
}

/**
 * Read view of the deploy strategy + edit affordance via a side sheet.
 * The strategy here is the trio of trigger mode, deploy branch, and
 * preview state — the three knobs that decide *when* and *from where* a
 * deploy lands. Approval policy is environment-scoped and lives on the
 * Controls section below.
 */
export function DeployStrategyCard({ app }: Props) {
  const t = useTranslations("apps.deployStrategy");
  const [open, setOpen] = useState(false);
  const mode: TriggerMode = (app.triggerMode as TriggerMode) || "manual";
  const Icon = MODE_ICONS[mode] ?? MODE_ICONS.manual;

  return (
    <section className="rounded-lg border p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-muted-foreground text-[11px] font-medium tracking-wide uppercase">
            {t("eyebrow")}
          </p>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="gap-1.5 py-1">
              <Icon className="size-3.5 text-[var(--brand-primary)]" />
              <span className="font-medium">{t(`modes.${mode}.label`)}</span>
            </Badge>
            <Badge variant="secondary" className="gap-1 font-mono text-[10px]">
              <GitBranchIcon className="size-3" />
              {app.deployBranch || app.defaultBranch}
            </Badge>
            {app.previewEnabled && (
              <Badge variant="outline" className="text-[10px]">
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
      </div>

      <EditStrategySheet app={app} open={open} onOpenChange={setOpen} />
    </section>
  );
}

function EditStrategySheet({
  app,
  open,
  onOpenChange,
}: {
  app: AstroliftRegisteredApp;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const t = useTranslations("apps.deployStrategy");
  const [triggerMode, setTriggerMode] = useState<TriggerMode>(app.triggerMode);
  const [deployBranch, setDeployBranch] = useState(app.deployBranch || app.defaultBranch);
  const [previewEnabled, setPreviewEnabled] = useState(app.previewEnabled);
  const [cronExpression, setCronExpression] = useState(app.cronExpression || "");

  const [save, { loading }] = useMutation<UpdateResp>(UPDATE_APP, {
    refetchQueries: [{ query: GET_APP, variables: { slug: app.slug } }],
    awaitRefetchQueries: true,
  });

  async function handleSave() {
    const { data } = await save({
      variables: {
        input: {
          id: app.id,
          triggerMode,
          deployBranch: deployBranch.trim() || null,
          previewEnabled,
          // The backend ignores cron_expression unless mode == 'cron',
          // and requires it when mode == 'cron'. Send the trimmed value
          // straight through; validation lives server-side.
          cronExpression: triggerMode === "cron" ? cronExpression.trim() : null,
        },
      },
    });
    if (data?.updateApp.ok) {
      toast.success(t("toastSaved"));
      onOpenChange(false);
    } else {
      toast.error(data?.updateApp.errors?.[0]?.message ?? t("toastFailed"));
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
              <span className="text-muted-foreground text-[11px]">{t("cronExpressionHelp")}</span>
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
            <span className="text-muted-foreground text-[11px]">
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
              <span className="text-muted-foreground block text-xs">{t("previewEnvironmentsHelp")}</span>
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
