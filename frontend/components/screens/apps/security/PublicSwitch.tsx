"use client";

import { GlobeIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AstroliftAppAccess } from "@/graphql/__generated__/schema";

import type { usePublicSwitch } from "./use-app-access";

const STATES = [
  "public",
  "gated",
  "no_gate",
  "not_envoy",
  "cluster_disallows",
  "restricted",
] as const;
type PublicState = (typeof STATES)[number];

function known(state: string): state is PublicState {
  return (STATES as readonly string[]).includes(state);
}

export interface PublicSwitchViewProps {
  access: Pick<AstroliftAppAccess, "appSlug" | "edgePublic" | "publicStates" | "restricted">;
  /** The switch, for an operator holding `app.access`; read-only without it. */
  control?: ReturnType<typeof usePublicSwitch>;
}

/**
 * Whether the app's platform URL answers without the edge login (#2504).
 * Off by default. Turning it on asks for confirmation and a fresh
 * verification; turning it off never does. Each cluster the app runs on
 * says what the switch means there: only the Envoy edge honours it, and a
 * cluster can refuse public apps outright.
 */
export function PublicSwitchView({ access, control }: PublicSwitchViewProps) {
  const t = useTranslations("apps.security.public");
  const { edgePublic, publicStates, restricted } = access;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div className="flex items-center gap-2 text-sm font-medium">
        <GlobeIcon className="size-4" />
        {t("title")}
        <Badge variant={edgePublic ? "destructive" : "secondary"} className="text-2xs font-normal">
          {edgePublic ? t("on") : t("off")}
        </Badge>
      </div>
      <p className="text-muted-foreground text-xs">{t("description")}</p>
      {publicStates.length > 0 && (
        <ul className="grid gap-1 text-xs">
          {publicStates.map((s) => (
            <li key={s.clusterSlug} className="flex min-w-0 flex-wrap gap-x-2">
              <span className="font-mono [overflow-wrap:anywhere]">{s.clusterSlug}</span>
              <span className="text-muted-foreground">
                {known(s.state) ? t(`states.${s.state}`) : s.state}
              </span>
            </li>
          ))}
        </ul>
      )}
      {control && (
        <div className="flex flex-col items-start gap-1.5">
          {edgePublic ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={control.saving}
              onClick={() => void control.onSet(false)}
            >
              {t("makePrivate")}
            </Button>
          ) : (
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={control.saving || restricted}
              onClick={() => control.setConfirming(true)}
            >
              {t("makePublic")}
            </Button>
          )}
          {restricted && !edgePublic && (
            <p className="text-muted-foreground text-xs">{t("restrictedHint")}</p>
          )}
          {control.refusal && (
            <p role="alert" className="text-destructive text-xs">
              {control.refusal}
            </p>
          )}
          <ConfirmDialog
            open={control.confirming}
            onOpenChange={control.setConfirming}
            title={t("confirmTitle", { app: access.appSlug })}
            description={t("confirmDescription")}
            confirmLabel={t("confirm")}
            destructive
            // Closes either way: a refusal shows under the button, not
            // behind the modal.
            onConfirm={async () => {
              await control.onSet(true);
            }}
          />
        </div>
      )}
    </div>
  );
}
