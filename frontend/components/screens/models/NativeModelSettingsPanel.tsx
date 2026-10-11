"use client";
import { useId } from "react";
import { useTranslations } from "next-intl";
import { UsersIcon } from "lucide-react";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ListPage } from "@/components/list/ListPage";
import type { ModelPage } from "./ModelSubscriptionsPanel";
import type { DedicatedModelApp } from "./shared-model-settings";

export type NativeModelSettingsPanelProps = {
  allowed: boolean;
  loading: boolean;
  reason: string | null;
  onRetry: () => void;
  name: string;
  onName: (value: string) => void;
  subscriptions: boolean;
  onSubscriptions: (value: boolean) => void;
  mode: "SHARED" | "DEDICATED";
  onMode: (value: "SHARED" | "DEDICATED") => void;
  app: DedicatedModelApp | null;
  apps: ModelPage<DedicatedModelApp>;
  onApp: (app: DedicatedModelApp) => void;
  canUpdate: boolean;
  busy: boolean;
  sent: boolean;
  outcome: "queued" | "removed" | null;
  error: string | null;
  review: "update" | "remove" | null;
  canConfirm: boolean;
  onReview: (kind: "update" | "remove") => void;
  onDismiss: () => void;
  onConfirm: () => Promise<boolean>;
};
export function NativeModelSettingsPanel(props: NativeModelSettingsPanelProps) {
  const id = useId(),
    t = useTranslations("models.native.details"),
    connect = useTranslations("models.native.connect"),
    inventory = useTranslations("models.shared.inventory"),
    placement = useTranslations("models.shared.placement"),
    management = useTranslations("models.shared.management"),
    common = useTranslations("models.shared.deployments");
  const locked = !props.allowed || props.busy || props.sent;
  return (
    <section id="model-settings" aria-labelledby={`${id}-title`} className="scroll-mt-20 space-y-4">
      <h2 id={`${id}-title`} className="text-lg font-semibold">
        {inventory("settings")}
      </h2>
      <p>{t("settingsHelp")}</p>
      {props.loading ? (
        <p role="status">{management("checking")}</p>
      ) : (
        props.reason && (
          <div role="status">
            <p className="break-words">{props.reason}</p>
            <Button variant="outline" onClick={props.onRetry}>
              {placement("retry")}
            </Button>
          </div>
        )
      )}
      {props.error && (
        <p role="alert" className="break-words">
          {props.error}
        </p>
      )}
      {props.outcome && <p role="status">{t(props.outcome)}</p>}
      {props.sent && !props.outcome && <p role="status">{connect("unconfirmed")}</p>}
      <fieldset disabled={locked} className="space-y-4">
        <Label htmlFor={`${id}-name`}>{connect("name")}</Label>
        <Input
          id={`${id}-name`}
          value={props.name}
          maxLength={128}
          onChange={(event) => props.onName(event.target.value)}
        />
        <Label className="flex gap-2">
          <input
            type="checkbox"
            checked={props.subscriptions}
            onChange={(event) => props.onSubscriptions(event.target.checked)}
          />
          {placement("allowSubscriptions")}
        </Label>
        <div className="flex gap-4">
          {(["SHARED", "DEDICATED"] as const).map((mode) => (
            <Label key={mode} className="flex gap-2">
              <input
                type="radio"
                name={`${id}-mode`}
                checked={props.mode === mode}
                onChange={() => props.onMode(mode)}
              />
              {inventory(mode === "SHARED" ? "shared" : "dedicated")}
            </Label>
          ))}
        </div>
        <p>{inventory("sharingHelp")}</p>
        {props.mode === "DEDICATED" && (
          <>
            <p>
              {props.app
                ? inventory("dedicatedApp", { app: props.app.name })
                : inventory("chooseDedicatedApp")}
            </p>
            <ListPage
              embedded
              {...props.apps}
              label={inventory("selectApp")}
              getRowId={(app) => app.id}
              columns={[
                {
                  id: "app",
                  header: inventory("selectApp"),
                  cell: (app) => (
                    <Button
                      variant="outline"
                      disabled={
                        locked || props.apps.loading || props.apps.stale || !!props.apps.error
                      }
                      onClick={() => props.onApp(app)}
                    >
                      {app.name}
                    </Button>
                  ),
                },
              ]}
              empty={{
                icon: <UsersIcon />,
                title: inventory("appsEmpty"),
                description: inventory("appsEmptyHelp"),
              }}
            />
          </>
        )}
      </fieldset>
      <div className="flex flex-wrap gap-2">
        <Button disabled={locked || !props.canUpdate} onClick={() => props.onReview("update")}>
          {t("saveSettings")}
        </Button>
        <Button variant="destructive" disabled={locked} onClick={() => props.onReview("remove")}>
          {t("remove")}
        </Button>
      </div>
      <ConfirmDialog
        open={props.review != null}
        onOpenChange={(open) => {
          if (!open) props.onDismiss();
        }}
        title={t(props.review === "remove" ? "remove" : "saveSettings")}
        description={
          props.review === "remove" ? (
            t("removeHelp")
          ) : (
            <span className="block space-y-2">
              <span className="block">{t("settingsHelp")}</span>
              <span className="block">
                {props.name} · {inventory(props.mode === "SHARED" ? "shared" : "dedicated")}
                {props.app ? ` · ${props.app.name}` : ""}
              </span>
              <span className="block">
                {placement("allowSubscriptions")}:{" "}
                {common(props.subscriptions ? "enabled" : "disabled")}
              </span>
            </span>
          )
        }
        confirmLabel={t(props.review === "remove" ? "remove" : "saveSettings")}
        confirmDisabled={!props.canConfirm}
        destructive={props.review === "remove"}
        onConfirm={props.onConfirm}
      />
    </section>
  );
}
